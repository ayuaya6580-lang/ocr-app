import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd
import time
from pypdf import PdfReader, PdfWriter
import io
import re

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="AI高速・高精度読み取り", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. 強力なJSON抽出関数（エラー回避の要）
# ==========================================
def extract_json(text):
    """
    AIの返答からJSON部分だけを無理やり抜き出す関数
    """
    try:
        # Markdown記法を削除
        text = text.replace("```json", "").replace("```", "").strip()
        
        # 波括弧 { } の一番外側を探す（余計な挨拶文をカットするため）
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
            
        return json.loads(text)
    except:
        return None

# ==========================================
# 3. 解析を行う関数
# ==========================================
def analyze_chunk(input_data, mime_type, chunk_info):
    genai.configure(api_key=GOOGLE_API_KEY)
    model_name = "gemini-flash-latest" 

    prompt = """
    以下の請求書・領収書データ（複数ページ）を読み取り、
    **全てのページに含まれる明細行**を抽出して、1つのJSONリストにまとめてください。
    
    出力は必ず以下のJSON形式のみにしてください。解説は不要です。
    {
      "items": [
        {
          "date": "YYYY-MM-DD",
          "company_name": "店名・仕入先",
          "jan_code": "JAN/品番",
          "product_name": "商品名",
          "quantity": "数量(数値)",
          "retail_price": "上代(数値)",
          "cost_price": "単価/下代(数値)",
          "line_total": "行合計(数値)",
          "wholesale_rate": "掛け率",
          "invoice_number": "インボイス番号"
        }
      ]
    }
    """
    
    # リトライ回数を増やす
    max_retries = 3
    for attempt in range(max_retries):
        try:
            model = genai.GenerativeModel(model_name)
            
            if mime_type == "application/pdf":
                content_part = {"mime_type": "application/pdf", "data": input_data}
                # トークン数を増やして切れにくくする
                response = model.generate_content(
                    [prompt, content_part], 
                    generation_config={"response_mime_type": "application/json"} 
                )
            else:
                response = model.generate_content(
                    [prompt, input_data],
                    generation_config={"response_mime_type": "application/json"}
                )

            # 強力な抽出関数を通す
            data = extract_json(response.text)
            
            if data:
                return data
            else:
                # JSONパース失敗ならリトライ対象にする
                raise Exception("JSON Parse Error")

        except Exception as e:
            time.sleep(5 * (attempt + 1)) # 待機時間を少し長めに
            continue
            
    return None

# ==========================================
# 4. 画面のデザイン
# ==========================================
st.title("⚡ AI高速・高精度読み取り（3ページ束ね処理）")
st.markdown("エラーを減らすため、**3ページずつ** 確実に処理します。")

uploaded_files = st.file_uploader(
    "ここにファイルをドラッグ＆ドロップ", 
    type=["pdf", "jpg", "png"], 
    accept_multiple_files=True
)

if uploaded_files:
    st.info("📄 準備完了。開始ボタンを押してください。")

    if st.button(f"読み取り開始 🚀", use_container_width=True):
        
        all_rows = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        error_log = []
        
        tasks = []

        # --- 準備フェーズ ---
        status_text.text("準備中: 最適なサイズに分割しています...")
        
        # ★ここが重要：安定重視で「3ページ」に変更
        CHUNK_SIZE = 3 
        
        for file in uploaded_files:
            if file.type == "application/pdf":
                try:
                    pdf_reader = PdfReader(file)
                    total_pages = len(pdf_reader.pages)
                    
                    for i in range(0, total_pages, CHUNK_SIZE):
                        pdf_writer = PdfWriter()
                        end_page = min(i + CHUNK_SIZE, total_pages)
                        
                        for p in range(i, end_page):
                            pdf_writer.add_page(pdf_reader.pages[p])
                        
                        with io.BytesIO() as output_stream:
                            pdf_writer.write(output_stream)
                            chunk_bytes = output_stream.getvalue()
                            
                            label = f"{file.name} (p{i+1}-{end_page})"
                            tasks.append({
                                "data": chunk_bytes,
                                "mime": "application/pdf",
                                "label": label
                            })
                except:
                    error_log.append(f"{file.name} 読み込み失敗")
            else:
                tasks.append({
                    "data": Image.open(file),
                    "mime": "image",
                    "label": file.name
                })

        # --- 実行フェーズ ---
        total_tasks = len(tasks)
        st.write(f"全 {total_tasks} 束の処理を開始します...")

        for idx, task in enumerate(tasks):
            status_text.text(f"🔍 解析中... {idx+1}/{total_tasks} : {task['label']}")
            
            # API制限対策（3ページごとなので少し休憩）
            time.sleep(2) 

            result = analyze_chunk(task['data'], task['mime'], task['label'])
            
            if result:
                items_list = []
                if isinstance(result, list):
                    items_list = result
                elif isinstance(result, dict):
                    items_list = result.get("items", [])
                    if not items_list and "product_name" in result:
                        items_list = [result]

                if items_list:
                    for item in items_list:
                        row = {
                            "ファイル/ページ": task['label'],
                            "日付": item.get("date"),
                            "仕入先": item.get("company_name"),
                            "JAN/品番": item.get("jan_code"),
                            "商品名": item.get("product_name"),
                            "数量": item.get("quantity"),
                            "上代": item.get("retail_price"),
                            "単価(下代)": item.get("cost_price"),
                            "金額(行合計)": item.get("line_total"),
                            "掛け率": item.get("wholesale_rate"),
                            "インボイスNo": item.get("invoice_number")
                        }
                        all_rows.append(row)
            else:
                error_log.append(f"{task['label']} - 読み取り失敗")

            progress_bar.progress((idx + 1) / total_tasks)

        status_text.success("完了しました！")

        if error_log:
            with st.expander(f"⚠️ {len(error_log)}件のエラーがありました"):
                for err in error_log:
                    st.write(err)
            
        if all_rows:
            df = pd.DataFrame(all_rows)
            
            desired_order = [
                "ファイル/ページ", "日付", "仕入先", "JAN/品番", "商品名", 
                "数量", "上代", "掛け率", "単価(下代)", "金額(行合計)", "インボイスNo"
            ]
            final_columns = [c for c in desired_order if c in df.columns]
            df = df[final_columns]
            
            st.subheader(f"📊 抽出データ ({len(df)}行)")
            st.dataframe(df)
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="CSV保存 💾",
                data=csv,
                file_name="high_accuracy_data.csv",
                mime="text/csv"
            )
        else:
            st.warning("データを抽出できませんでした。")
