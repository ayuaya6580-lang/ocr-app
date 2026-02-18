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
st.set_page_config(page_title="AI高速一括読み取り", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. 解析を行う関数（5ページまとめて処理）
# ==========================================
def analyze_chunk(input_data, mime_type, chunk_info):
    genai.configure(api_key=GOOGLE_API_KEY)
    model_name = "gemini-flash-latest" 

    # 複数ページをまとめて処理するためのプロンプト
    prompt = """
    以下の請求書・領収書データ（複数ページの場合あり）を読み取り、
    **全てのページに含まれる明細行**を抽出して、1つのJSONリストにまとめてください。
    
    Markdown記法（```json 等）は不要です。
    出力形式:
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
        },
        ... (全明細を列挙)
      ]
    }
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
            
            # JSON変換
            data = json.loads(cleaned_text)
            return data

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "503" in error_msg:
                wait = 15 * (attempt + 1) # 混雑時は少し長めに待つ
                time.sleep(wait)
                continue
            return None
    return None

# ==========================================
# 3. 画面のデザイン
# ==========================================
st.title("⚡ AI高速一括読み取り（5ページ同時処理）")
st.markdown("135ページのような大量データも、**5ページずつ束ねて処理**することで高速化します。")

uploaded_files = st.file_uploader(
    "ここにファイルをドラッグ＆ドロップ (PDF推奨)", 
    type=["pdf", "jpg", "png"], 
    accept_multiple_files=True
)

if uploaded_files:
    # ファイル数というより、PDFの中身が重要なので確認
    st.info("📄 ファイルがセットされました。開始ボタンを押すと高速解析します。")

    if st.button(f"高速読み取り開始 🚀", use_container_width=True):
        
        all_rows = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        error_log = []
        
        # 処理対象の全チャンクを作成するリスト
        # [ (pdf_bytes, "filename_p1-5"), (pdf_bytes, "filename_p6-10")... ]
        tasks = []

        # --- 準備フェーズ：PDFを5ページごとに分割する ---
        status_text.text("準備中: ページを切り分けています...")
        
        for file in uploaded_files:
            if file.type == "application/pdf":
                try:
                    pdf_reader = PdfReader(file)
                    total_pages = len(pdf_reader.pages)
                    
                    # 5ページずつループ
                    chunk_size = 10
                    for i in range(0, total_pages, chunk_size):
                        # 新しいPDFを作る
                        pdf_writer = PdfWriter()
                        # i番目から、i+5番目まで（または最後まで）を追加
                        end_page = min(i + chunk_size, total_pages)
                        
                        for p in range(i, end_page):
                            pdf_writer.add_page(pdf_reader.pages[p])
                        
                        # バイトデータに変換
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
                    error_log.append(f"{file.name} の読み込みに失敗")
            else:
                # 画像の場合はそのまま1つとして扱う
                tasks.append({
                    "data": Image.open(file),
                    "mime": "image",
                    "label": file.name
                })

        # --- 実行フェーズ ---
        total_tasks = len(tasks)
        st.write(f"合計 {total_tasks} 回のAI解析を実行します...")

        for idx, task in enumerate(tasks):
            status_text.text(f"🔥 高速解析中... {idx+1}/{total_tasks} : {task['label']}")
            
            # API制限対策の短い休憩（5ページごとなので頻度は低い）
            time.sleep(3) 

            result = analyze_chunk(task['data'], task['mime'], task['label'])
            
            # 結果の取り出し
            if result:
                # リスト形式で返ってくるか、辞書の中の"items"かを確認
                items_list = []
                if isinstance(result, list):
                    items_list = result
                elif isinstance(result, dict):
                    items_list = result.get("items", [])
                    # もしitemsがなく、直下にデータがある場合の保険
                    if not items_list and "product_name" in result:
                        items_list = [result]

                if items_list:
                    for item in items_list:
                        # 必要な項目を整理して追加
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
                    # データなし（明細がなかったページなど）
                    pass
            else:
                error_log.append(f"{task['label']} - 解析失敗")

            progress_bar.progress((idx + 1) / total_tasks)

        status_text.success("✅ 完了しました！")

        # 結果表示
        if error_log:
            with st.expander("⚠️ うまく読めなかった箇所"):
                for err in error_log:
                    st.write(err)
            
        if all_rows:
            df = pd.DataFrame(all_rows)
            
            # 列の並び替え
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
                file_name="fast_bulk_data.csv",
                mime="text/csv"
            )
        else:
            st.warning("データを抽出できませんでした。")

