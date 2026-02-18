import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd
import time
from pypdf import PdfReader, PdfWriter
import io

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="AI一括伝票読み取り", layout="wide")

# APIキーの読み込み
try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。StreamlitのSecretsを設定してください。")
    st.stop()

# ==========================================
# 2. 解析を行う関数（単一ページ処理用）
# ==========================================
def analyze_page(input_data, mime_type, page_label):
    genai.configure(api_key=GOOGLE_API_KEY)
    model_name = "gemini-flash-latest" 

    prompt = """
    以下のレシート・請求書データを読み取り、純粋なJSON形式のみを出力してください。
    Markdown記法（```json 等）は含めないでください。
    必ず { ... } で始まる単一のオブジェクトを返してください。
    
    【全体情報】
    - date (日付: YYYY-MM-DD)
    - company_name (仕入先・店名)
    - total_amount (伝票合計金額: 数値のみ)
    - invoice_number (インボイス番号)
    
    【明細リスト (items)】
    表に含まれる全ての商品行を抽出してください。
    - jan_code (JAN/品番)
    - product_name (商品名)
    - quantity (数量: 数値)
    - retail_price (上代/定価: 数値)
    - cost_price (単価/下代: 数値)
    - line_total (金額/行合計: 数値)
    - wholesale_rate (掛け率)
    """
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            model = genai.GenerativeModel(model_name)
            
            if mime_type == "application/pdf":
                content_part = {"mime_type": "application/pdf", "data": input_data}
                response = model.generate_content([prompt, content_part], request_options={"timeout": 600})
            else:
                response = model.generate_content([prompt, input_data], request_options={"timeout": 600})

            text = response.text
            cleaned_text = text.replace("```json", "").replace("```", "").strip()
            
            # JSON変換トライ
            return json.loads(cleaned_text)

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "503" in error_msg:
                wait = 10 * (attempt + 1)
                time.sleep(wait)
                continue
            else:
                # 致命的なエラー以外はNoneを返して次へ
                return None
    return None

# ==========================================
# 3. 画面のデザイン
# ==========================================
st.title("📂 AI伝票一括読み取り（全ページ分割処理版）")
st.markdown("PDFが複数ページある場合、**1ページずつ自動で切り離して**解析します。")

uploaded_files = st.file_uploader(
    "ここにファイルをまとめて放り込んでください (画像・PDF)", 
    type=["jpg", "png", "jpeg", "pdf"], 
    accept_multiple_files=True
)

if uploaded_files:
    file_count = len(uploaded_files)
    st.info(f"📄 {file_count} 件のファイルがセットされました")

    if st.button(f"読み取り開始 🚀", use_container_width=True):
        
        all_rows = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        error_log = []
        
        # 全体の進捗計算用（ファイル数ではなく、ページ総数で考えたいが、まずは簡易的に）
        total_steps = file_count
        current_step = 0

        for file_index, file in enumerate(uploaded_files):
            file_name = file.name
            mime_type = "application/pdf" if file.type == "application/pdf" else "image"
            
            # --- PDFの場合：ページごとに分解してループ ---
            if mime_type == "application/pdf":
                try:
                    pdf_reader = PdfReader(file)
                    num_pages = len(pdf_reader.pages)
                    
                    status_text.text(f"処理中: {file_name} (全{num_pages}ページ)...")
                    
                    for page_num in range(num_pages):
                        # 進捗表示詳細
                        status_text.text(f"処理中: {file_name} - {page_num+1} / {num_pages} ページ目 ⏳")
                        
                        # 1ページだけ取り出して新しいPDFデータ(bytes)を作る
                        pdf_writer = PdfWriter()
                        pdf_writer.add_page(pdf_reader.pages[page_num])
                        
                        with io.BytesIO() as output_stream:
                            pdf_writer.write(output_stream)
                            page_bytes = output_stream.getvalue()
                            
                            # ここでAIに送信！
                            time.sleep(2) # 休憩
                            result = analyze_page(page_bytes, "application/pdf", f"{file_name}_p{page_num+1}")
                            
                            # 結果の保存処理
                            if isinstance(result, list): # リスト対策
                                result = result[0] if len(result) > 0 else None

                            if result:
                                header_info = {
                                    "ファイル名": f"{file_name} (p{page_num+1})",
                                    "日付": result.get("date"),
                                    "仕入先": result.get("company_name"),
                                    "伝票合計": result.get("total_amount"),
                                    "インボイスNo": result.get("invoice_number"),
                                }
                                items = result.get("items", [])
                                if items:
                                    for item in items:
                                        row = header_info.copy()
                                        row.update({
                                            "JAN/品番": item.get("jan_code"),
                                            "商品名": item.get("product_name"),
                                            "数量": item.get("quantity"),
                                            "上代": item.get("retail_price"),
                                            "単価(下代)": item.get("cost_price"),
                                            "金額(行合計)": item.get("line_total"),
                                            "掛け率": item.get("wholesale_rate")
                                        })
                                        all_rows.append(row)
                                else:
                                    row = header_info.copy()
                                    row.update({"商品名": "（明細なし）"})
                                    all_rows.append(row)
                            else:
                                error_log.append(f"{file_name} (p{page_num+1}) - 読み取り失敗")

                except Exception as e:
                    error_log.append(f"{file_name} - PDF処理エラー: {e}")

            # --- 画像の場合：そのまま処理 ---
            else:
                status_text.text(f"処理中: {file_name} (画像)...")
                time.sleep(2)
                try:
                    image = Image.open(file)
                    result = analyze_page(image, "image", file_name)
                    
                    if isinstance(result, list):
                        result = result[0] if len(result) > 0 else None

                    if result:
                        header_info = {
                            "ファイル名": file_name,
                            "日付": result.get("date"),
                            "仕入先": result.get("company_name"),
                            "伝票合計": result.get("total_amount"),
                            "インボイスNo": result.get("invoice_number"),
                        }
                        items = result.get("items", [])
                        if items:
                            for item in items:
                                row = header_info.copy()
                                row.update({
                                    "JAN/品番": item.get("jan_code"),
                                    "商品名": item.get("product_name"),
                                    "数量": item.get("quantity"),
                                    "上代": item.get("retail_price"),
                                    "単価(下代)": item.get("cost_price"),
                                    "金額(行合計)": item.get("line_total"),
                                    "掛け率": item.get("wholesale_rate")
                                })
                                all_rows.append(row)
                        else:
                            row = header_info.copy()
                            row.update({"商品名": "（明細なし）"})
                            all_rows.append(row)
                    else:
                        error_log.append(f"{file_name} - 読み取り失敗")
                except Exception as e:
                    error_log.append(f"{file_name} - 画像エラー: {e}")

            # プログレスバー更新
            current_step += 1
            progress_bar.progress(current_step / total_steps)

        status_text.success("すべての処理が完了しました！")

        # 結果表示
        if error_log:
            with st.expander("⚠️ 読み取れなかったページ"):
                for err in error_log:
                    st.write(err)
            
        if all_rows:
            df = pd.DataFrame(all_rows)
            
            desired_order = [
                "ファイル名", "日付", "仕入先", "JAN/品番", "商品名", 
                "数量", "上代", "掛け率", "単価(下代)", "金額(行合計)", "伝票合計", "インボイスNo"
            ]
            final_columns = [c for c in desired_order if c in df.columns]
            df = df[final_columns]
            
            st.subheader("📊 統合データ")
            st.dataframe(df)
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="CSV保存 💾",
                data=csv,
                file_name="bulk_data_pages.csv",
                mime="text/csv"
            )
        else:
            st.error("データを読み取れませんでした。")
