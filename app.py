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
st.set_page_config(page_title="AI高速・完全読み取り", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. JSON抽出・修復関数
# ==========================================
def extract_json(text):
    """
    AIの返答からJSON部分を抜き出し、多少の壊れなら修復を試みる
    """
    try:
        # 余計な文字を削除
        text = text.replace("```json", "").replace("```", "").strip()
        
        # { } の範囲を探す
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
        
        return json.loads(text)
    except:
        # 末尾が切れている場合の簡易修復（閉じカッコを補う）
        try:
            if text.strip().endswith("]"): 
                text += "}" 
            elif text.strip().endswith("}") == False:
                text += "]}"
            return json.loads(text)
        except:
            return None

# ==========================================
# 3. 解析を行うコア関数
# ==========================================
def call_ai_api(input_data, mime_type):
    genai.configure(api_key=GOOGLE_API_KEY)
    model_name = "gemini-flash-latest" 

    prompt = """
    以下の請求書・領収書データを読み取り、明細行を抽出してJSONリストにまとめてください。
    
    出力形式:
    {
      "items": [
        {
          "date": "YYYY-MM-DD",
          "company_name": "店名",
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
    
    model = genai.GenerativeModel(model_name)
    
    # リトライロジック
    for attempt in range(3):
        try:
            if mime_type == "application/pdf":
                content_part = {"mime_type": "application/pdf", "data": input_data}
                response = model.generate_content(
                    [prompt, content_part], 
                    generation_config={"response_mime_type": "application/json"} 
                )
            else:
                response = model.generate_content(
                    [prompt, input_data],
                    generation_config={"response_mime_type": "application/json"}
                )
            
            data = extract_json(response.text)
            if data: return data
            
        except Exception as e:
            time.sleep(3 * (attempt + 1)) # エラー時は少し待つ
            continue
            
    return None

# ==========================================
# 4. 画面のデザイン・メイン処理
# ==========================================
st.title("🛡️ AI高速・完全読み取り（自動リトライ機能付）")
st.markdown("基本は3ページまとめて高速処理し、**失敗した箇所だけ自動で1ページずつ丁寧に読み直します**。")

uploaded_files = st.file_uploader(
    "ここにファイルをドラッグ＆ドロップ", 
    type=["pdf", "jpg", "png"], 
    accept_multiple_files=True
)

if uploaded_files:
    if st.button(f"読み取り開始 🚀", use_container_width=True):
        
        all_rows = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        error_log = []
        
        # 処理タスクの作成
        tasks = []
        for f in uploaded_files:
            tasks.append(f)

        total_files = len(tasks)

        for file_idx, file in enumerate(tasks):
            file_name = file.name
            
            # --- 画像の場合 ---
            if file.type != "application/pdf":
                status_text.text(f"処理中: {file_name} (画像)...")
                image = Image.open(file)
                result = call_ai_api(image, "image")
                
                if result:
                    # データ保存処理
                    items = result.get("items", []) if isinstance(result, dict) else []
                    for item in items:
                        item["ファイル/ページ"] = file_name
                        all_rows.append(item)
                else:
                    error_log.append(f"❌ {file_name} (画像読み取り失敗)")
                
                progress_bar.progress((file_idx + 1) / total_files)
                continue

            # --- PDFの場合（ここが重要） ---
            try:
                pdf_reader = PdfReader(file)
                total_pages = len(pdf_reader.pages)
                chunk_size = 3 # 基本は3ページずつ
                
                for i in range(0, total_pages, chunk_size):
                    end_page = min(i + chunk_size, total_pages)
                    page_label = f"{file_name} (p{i+1}-{end_page})"
                    
                    status_text.text(f"⚡ 高速処理中: {page_label} ...")
                    
                    # 3ページ分のPDFを作成
                    pdf_writer = PdfWriter()
                    for p in range(i, end_page):
                        pdf_writer.add_page(pdf_reader.pages[p])
                    
                    chunk_bytes = io.BytesIO()
                    pdf_writer.write(chunk_bytes)
                    chunk_data = chunk_bytes.getvalue()
                    
                    # ★まずは3ページまとめてトライ！
                    time.sleep(1) # 少し休憩
                    result = call_ai_api(chunk_data, "application/pdf")
                    
                    if result:
                        # 成功！
                        items = result.get("items", []) if isinstance(result, dict) else []
                        for item in items:
                            item["ファイル/ページ"] = page_label
                            all_rows.append(item)
                    else:
                        # ★失敗！ここから「1ページずつ再挑戦モード」発動
                        st.warning(f"⚠️ {page_label} の一括読み取りに失敗。1ページずつ丁寧に読み直します...")
                        
                        for retry_p in range(i, end_page):
                            single_label = f"{file_name} (p{retry_p+1})"
                            status_text.text(f"🐢 救済処理中: {single_label} ...")
                            
                            # 1ページだけのPDF作成
                            single_writer = PdfWriter()
                            single_writer.add_page(pdf_reader.pages[retry_p])
                            single_bytes = io.BytesIO()
                            single_writer.write(single_bytes)
                            
                            time.sleep(2) # 念入りに休憩
                            single_result = call_ai_api(single_bytes.getvalue(), "application/pdf")
                            
                            if single_result:
                                items = single_result.get("items", []) if isinstance(single_result, dict) else []
                                for item in items:
                                    item["ファイル/ページ"] = single_label
                                    all_rows.append(item)
                            else:
                                error_log.append(f"❌ {single_label} (完全読み取り不可)")

            except Exception as e:
                error_log.append(f"❌ {file_name} 全体エラー: {e}")

            progress_bar.progress((file_idx + 1) / total_files)

        status_text.success("すべての処理が完了しました！")

        if error_log:
            with st.expander(f"⚠️ 最終的に読み取れなかった箇所 ({len(error_log)}件)"):
                for err in error_log:
                    st.write(err)
            
        if all_rows:
            # データフレーム作成と整形
            df = pd.DataFrame(all_rows)
            
            # 列の存在確認をしてから並べ替え
            cols = [
                "ファイル/ページ", "date", "company_name", "jan_code", "product_name", 
                "quantity", "retail_price", "wholesale_rate", "cost_price", "line_total", "invoice_number"
            ]
            # 日本語表記へのマッピング
            col_map = {
                "date": "日付", "company_name": "仕入先", "jan_code": "JAN/品番", 
                "product_name": "商品名", "quantity": "数量", "retail_price": "上代", 
                "wholesale_rate": "掛け率", "cost_price": "単価(下代)", 
                "line_total": "金額(行合計)", "invoice_number": "インボイスNo"
            }
            
            # 存在する列だけ残す
            existing_cols = [c for c in cols if c in df.columns]
            df = df[existing_cols]
            df = df.rename(columns=col_map)
            
            st.subheader(f"📊 抽出データ ({len(df)}行)")
            st.dataframe(df)
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="CSV保存 💾",
                data=csv,
                file_name="final_data.csv",
                mime="text/csv"
            )
        else:
            st.error("データを1件も抽出できませんでした。")
