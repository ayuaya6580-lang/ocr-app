import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
import time
from pypdf import PdfReader, PdfWriter
import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="AI並列高速読み取り", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. 解析を行う関数（1ページ単位・リトライ付）
# ==========================================
def analyze_single_page(page_data, page_label, mime_type="application/pdf"):
    genai.configure(api_key=GOOGLE_API_KEY)
    
    # ★修正箇所：あなたの環境で動くモデル名に戻しました
    model_name = "gemini-flash-latest" 

    prompt = """
    この伝票画像の**明細行のみ**を抽出し、以下のJSON形式で出力してください。
    余計な解説やMarkdown記法（```jsonなど）は不要です。
    
    {
      "items": [
        {
          "date": "YYYY-MM-DD",
          "company_name": "仕入先店名",
          "product_name": "商品名",
          "quantity": "数量(数値)",
          "cost_price": "単価(下代/数値)",
          "line_total": "金額(行合計/数値)",
          "wholesale_rate": "掛け率",
          "invoice_number": "インボイスNo"
        }
      ]
    }
    """
    
    # リトライ回数（最大3回）
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            model = genai.GenerativeModel(model_name)
            
            # データ送信
            if mime_type == "application/pdf":
                content_part = {"mime_type": "application/pdf", "data": page_data}
                response = model.generate_content([prompt, content_part])
            else:
                response = model.generate_content([prompt, page_data])

            # JSON抽出
            text = response.text
            text = text.replace("```json", "").replace("```", "").strip()
            
            # まれに余計な文字がつく場合のクリーニング
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                text = match.group(0)
                
            return json.loads(text)

        except Exception as e:
            error_msg = str(e)
            # 「429 (使いすぎ)」エラーなら少し待って再開
            if "429" in error_msg or "ResourceExhausted" in error_msg:
                time.sleep(5 * (attempt + 1))
                continue
            elif attempt < max_retries - 1:
                time.sleep(2)
                continue
            else:
                return {"error": f"{error_msg}"}
            
    return None

# ==========================================
# 3. メイン処理（並列実行）
# ==========================================
st.title("🚀 AI並列高速読み取りシステム")
st.markdown("1ページずつ確実に、かつ**複数ページ同時に**処理します。")

uploaded_files = st.file_uploader(
    "ここにファイルをドラッグ＆ドロップ", 
    type=["pdf", "jpg", "png"], 
    accept_multiple_files=True
)

if uploaded_files:
    if st.button(f"高速読み取り開始 🚀", use_container_width=True):
        
        all_rows = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        error_log = []
        
        tasks = []
        
        # --- 準備：全ページをタスクに分解 ---
        status_text.text("準備中: ページを分解しています...")
        
        for file in uploaded_files:
            if file.type == "application/pdf":
                try:
                    pdf_reader = PdfReader(file)
                    for i, page in enumerate(pdf_reader.pages):
                        # 1ページずつ切り出す
                        pdf_writer = PdfWriter()
                        pdf_writer.add_page(page)
                        with io.BytesIO() as output:
                            pdf_writer.write(output)
                            page_bytes = output.getvalue()
                            
                            tasks.append({
                                "data": page_bytes,
                                "label": f"{file.name} (p{i+1})",
                                "mime": "application/pdf"
                            })
                except:
                    error_log.append(f"{file.name} の読み込みに失敗")
            else:
                # 画像の場合
                tasks.append({
                    "data": Image.open(file),
                    "label": file.name,
                    "mime": "image"
                })

        total_tasks = len(tasks)
        st.write(f"合計 {total_tasks} ページを並列処理します...")

        # --- 並列実行フェーズ ---
        # 安定重視で「2並列」に設定（エラー429回避のため）
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_to_task = {
                executor.submit(analyze_single_page, t["data"], t["label"], t["mime"]): t 
                for t in tasks
            }
            
            completed_count = 0
            
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                completed_count += 1
                
                status_text.text(f"処理中... {completed_count}/{total_tasks} 完了 ({task['label']})")
                progress_bar.progress(completed_count / total_tasks)
                
                try:
                    result = future.result()
                    
                    if isinstance(result, dict) and "error" in result:
                        error_log.append(f"{task['label']} - {result['error']}")
                    
                    elif result and "items" in result:
                        items = result.get("items", [])
                        if items:
                            for item in items:
                                row = {
                                    "ページ": task['label'],
                                    "日付": item.get("date"),
                                    "仕入先": item.get("company_name"),
                                    "JAN": item.get("jan_code"),
                                    "商品名": item.get("product_name"),
                                    "数量": item.get("quantity"),
                                    "単価": item.get("cost_price"),
                                    "金額": item.get("line_total"),
                                    "掛け率": item.get("wholesale_rate"),
                                    "インボイス": item.get("invoice_number")
                                }
                                all_rows.append(row)
                    else:
                        # データなし、または読み取り失敗
                        pass
                except Exception as e:
                    error_log.append(f"{task['label']} - システムエラー: {e}")

        status_text.success("🎉 すべての処理が完了しました！")

        # --- 結果表示 ---
        if error_log:
            with st.expander(f"⚠️ 読み取れなかったページ ({len(error_log)}件)"):
                for err in error_log:
                    st.write(err)
            
        if all_rows:
            df = pd.DataFrame(all_rows)
            
            cols = ["ページ", "日付", "仕入先", "JAN", "商品名", "数量", "単価", "金額", "掛け率", "インボイス"]
            valid_cols = [c for c in cols if c in df.columns]
            df = df[valid_cols]
            
            st.subheader(f"📊 抽出結果: {len(df)}行")
            st.dataframe(df)
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="CSVデータを保存 💾",
                data=csv,
                file_name="parallel_data.csv",
                mime="text/csv"
            )
        else:
            st.warning("データが見つかりませんでした。")
