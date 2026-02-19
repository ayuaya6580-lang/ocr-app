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
st.set_page_config(page_title="AI並列高速読み取り(安定版)", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. 解析を行う関数（1ページ単位・強力リトライ付）
# ==========================================
def analyze_single_page(page_data, page_label, mime_type="application/pdf"):
    genai.configure(api_key=GOOGLE_API_KEY)
    # 安定動作するモデルを指定
    model_name = "gemini-1.5-flash" 

    prompt = """
    この伝票画像の**明細行のみ**を抽出し、以下のJSON形式で出力してください。
    
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
    
    model = genai.GenerativeModel(model_name)
    
    # リトライ回数（最大5回まで粘る）
    max_retries = 5
    
    for attempt in range(max_retries):
        try:
            # データ送信
            if mime_type == "application/pdf":
                content_part = {"mime_type": "application/pdf", "data": page_data}
                response = model.generate_content(
                    [prompt, content_part],
                    generation_config={"response_mime_type": "application/json"}
                )
            else:
                response = model.generate_content(
                    [prompt, page_data],
                    generation_config={"response_mime_type": "application/json"}
                )

            return json.loads(response.text)

        except Exception as e:
            error_msg = str(e)
            # 「429 (使いすぎ)」エラーが出たら、長く休憩して再開
            if "429" in error_msg or "ResourceExhausted" in error_msg:
                wait_time = 20 * (attempt + 1) # 20秒, 40秒...と待機時間を増やす
                time.sleep(wait_time)
                continue # 再トライ
            elif attempt < max_retries - 1:
                # その他のエラーも少し待って再トライ
                time.sleep(5)
                continue
            else:
                # 最後までダメだった場合、エラー理由を返す
                return {"error": f"{error_msg}"}
            
    return None

# ==========================================
# 3. メイン処理（並列実行）
# ==========================================
st.title("🛡️ AI並列高速読み取り（安定版）")
st.markdown("速度を調整しながら、エラーが出ても**自動で待機・再開**して最後まで読み切ります。")

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
                tasks.append({
                    "data": Image.open(file),
                    "label": file.name,
                    "mime": "image"
                })

        total_tasks = len(tasks)
        st.write(f"合計 {total_tasks} ページを処理します...")

        # --- 並列実行フェーズ ---
        # ★重要：並列数を2に制限して、API制限を回避する
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
                    
                    # エラーメッセージが返ってきた場合
                    if isinstance(result, dict) and "error" in result:
                        error_log.append(f"{task['label']} - {result['error']}")
                    
                    # 正常にアイテムが返ってきた場合
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
                        # 明細なし、または空の結果
                        pass
                        
                except Exception as e:
                    error_log.append(f"{task['label']} - システムエラー: {e}")

        status_text.success("🎉 すべての処理が完了しました！")

        # --- 結果表示 ---
        if error_log:
            with st.expander(f"⚠️ 読み取れなかったページ ({len(error_log)}件)"):
                st.write("もし '429' エラーが多い場合は、少し時間をおいてから再試行してください。")
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
                file_name="stable_data.csv",
                mime="text/csv"
            )
        else:
            st.warning("データが見つかりませんでした。")
