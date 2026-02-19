import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
import time
from pypdf import PdfReader, PdfWriter
import io
import re
import gc
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="AI確実読み取り(API制限対策版)", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. ユーティリティ関数
# ==========================================
def extract_json_safe(text):
    text = text.strip()
    text = text.replace("```json", "").replace("```", "")
    
    match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
    if match:
        text = match.group(0)
    
    try:
        return json.loads(text)
    except:
        try:
            if text.startswith("[") and not text.endswith("]"):
                return json.loads(text + "]")
            if text.startswith("{") and not text.endswith("}"):
                return json.loads(text + "}")
        except:
            pass
    return None

# ==========================================
# 3. 解析関数
# ==========================================
def analyze_content(content, mode, source_label):
    genai.configure(api_key=GOOGLE_API_KEY)
    
    # ★重要：無料枠でも比較的制限が緩いモデルを使用
    model_name = "gemini-flash-latest"

    if mode == "text":
        prompt = f"""
        以下の「テキストデータ」から請求書・納品書の明細行を探し出し、JSONリストに変換してください。
        
        【テキストデータ】
        {content}
        
        【出力ルール】
        - JSONのみ出力。
        - キー: date, company_name, product_name, quantity, cost_price, line_total, invoice_number
        """
        input_data = prompt
    else:
        prompt = """
        画像を読み取り、明細行をJSONリストのみで出力してください。
        キー: date, company_name, product_name, quantity, cost_price, line_total, invoice_number
        """
        input_data = [prompt, content]

    # リトライ処理（回数を増やし、待機時間を長くする）
    for attempt in range(5):
        try:
            model = genai.GenerativeModel(model_name)
            
            if mode == "text":
                response = model.generate_content(input_data)
            else:
                response = model.generate_content(input_data)

            return {"raw": response.text, "data": extract_json_safe(response.text)}

        except Exception as e:
            error_msg = str(e)
            
            # 429エラー（使いすぎ）の場合、長めに待機
            if "429" in error_msg or "429" in str(error_msg):
                wait_time = 20 + (attempt * 10) # 20秒, 30秒, 40秒...と待つ
                time.sleep(wait_time)
                continue
            
            # その他のエラー
            time.sleep(5)
            if attempt == 4: # 最後のトライでもダメならエラーを返す
                return {"error": str(e)}
    
    return None

# ==========================================
# 4. メイン処理
# ==========================================
st.title("🛡️ AI確実読み取り (API制限対策版)")
st.markdown("速度を落として（2並列）、エラー429（使用制限）を回避しながら確実に処理します。")

uploaded_files = st.file_uploader(
    "ファイルをドラッグ＆ドロップ", 
    type=["pdf", "jpg", "png"], 
    accept_multiple_files=True
)

if uploaded_files:
    if st.button(f"読み取り開始 🚀", use_container_width=True):
        
        all_rows = []
        debug_logs = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        tasks = []
        status_text.text("ファイルを解析中...")
        
        for file in uploaded_files:
            if file.type == "application/pdf":
                try:
                    pdf_reader = PdfReader(file)
                    for i, page in enumerate(pdf_reader.pages):
                        extracted_text = ""
                        try:
                            extracted_text = page.extract_text()
                        except:
                            pass
                        
                        if extracted_text and len(extracted_text) > 50: 
                            tasks.append({
                                "type": "text",
                                "content": extracted_text,
                                "label": f"{file.name} (p{i+1}) [Text]"
                            })
                        else:
                            writer = PdfWriter()
                            writer.add_page(page)
                            with io.BytesIO() as output:
                                writer.write(output)
                                pdf_bytes = output.getvalue()
                            
                            tasks.append({
                                "type": "pdf_image",
                                "content": {"mime_type": "application/pdf", "data": pdf_bytes},
                                "label": f"{file.name} (p{i+1}) [Img]"
                            })
                except:
                    debug_logs.append(f"{file.name}: ファイル読み込みエラー")
            else:
                tasks.append({
                    "type": "image",
                    "content": Image.open(file),
                    "label": f"{file.name} [Img]"
                })

        total_tasks = len(tasks)
        st.write(f"処理対象: {total_tasks} ページ")

        # ★★★ 修正箇所：並列数を「2」に制限 ★★★
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_to_task = {
                executor.submit(analyze_content, t["content"], t["type"], t["label"]): t 
                for t in tasks
            }
            
            completed = 0
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                completed += 1
                progress_bar.progress(completed / total_tasks)
                status_text.text(f"処理中... {completed}/{total_tasks}: {task['label']}")
                
                try:
                    result = future.result()
                    
                    if result and "error" in result:
                        debug_logs.append(f"❌ {task['label']}: エラー {result['error']}")
                    elif result and result.get("data"):
                        data = result["data"]
                        items = data if isinstance(data, list) else data.get("items", [])
                        
                        if not items and isinstance(data, dict):
                             items = [data]

                        for item in items:
                            if isinstance(item, dict):
                                row = {
                                    "ページ": task['label'],
                                    "日付": item.get("date"),
                                    "仕入先": item.get("company_name"),
                                    "商品名": item.get("product_name"),
                                    "数量": item.get("quantity"),
                                    "単価": item.get("cost_price"),
                                    "金額": item.get("line_total"),
                                    "インボイス": item.get("invoice_number")
                                }
                                all_rows.append(row)
                    else:
                        raw_text = result.get("raw", "")[:100] if result else "None"
                        debug_logs.append(f"⚠️ {task['label']}: データ抽出失敗 ({raw_text}...)")

                except Exception as e:
                    debug_logs.append(f"❌ {task['label']}: システムエラー {e}")
                
                # メモリ解放と待機
                gc.collect()
                time.sleep(2) # ★1処理ごとに2秒休む

        status_text.success("完了！")

        if debug_logs:
            with st.expander(f"⚠️ 解析ログ ({len(debug_logs)}件 - クリックして確認)"):
                for log in debug_logs:
                    st.text(log)

        if all_rows:
            df = pd.DataFrame(all_rows)
            cols = ["ページ", "日付", "仕入先", "JAN", "商品名", "数量", "単価", "金額", "掛け率", "インボイス"]
            valid_cols = [c for c in cols if c in df.columns]
            df = df[valid_cols]

            st.subheader(f"📊 抽出データ ({len(df)}行)")
            st.dataframe(df)
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSV保存 💾", csv, "final_stable_data.csv", "text/csv")
        else:
            st.error("データが1件も抽出できませんでした。")
