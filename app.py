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

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="AI高速・完全読み取り(Robust)", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. 頑丈なJSON抽出関数
# ==========================================
def extract_json(text):
    """
    AIの返答からJSON部分を強力に抜き出す
    """
    try:
        # 1. 素直に変換できるかトライ
        return json.loads(text)
    except:
        pass

    try:
        # 2. Markdown記法 (```json ... ```) を削除してトライ
        cleaned = text.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)
    except:
        pass

    try:
        # 3. 波カッコ { ... } または リスト [ ... ] の範囲を正規表現で無理やり抽出
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            extracted = match.group(0)
            return json.loads(extracted)
    except:
        pass
        
    return None

# ==========================================
# 3. 解析関数（1ページ単位）
# ==========================================
def analyze_page_task(page_data, page_label):
    genai.configure(api_key=GOOGLE_API_KEY)
    # 動作確認済みのモデル名
    model_name = "gemini-flash-latest" 

    prompt = """
    この伝票画像の**明細行のみ**を抽出し、JSONデータとして出力してください。
    余計な挨拶や解説は不要です。
    
    【出力フォーマット】
    [
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
    """
    
    # エラー詳細を返すために辞書で管理
    result_info = {"success": False, "data": [], "error": None}

    for attempt in range(3):
        try:
            model = genai.GenerativeModel(model_name)
            
            content_part = {"mime_type": "application/pdf", "data": page_data}
            
            # JSONモードを強制せず、テキストとして受け取ってから抽出する（回避策）
            response = model.generate_content([prompt, content_part])
            
            # データ抽出トライ
            extracted_data = extract_json(response.text)
            
            if extracted_data:
                # 辞書形式 {"items": [...]} で来た場合と、リスト [...] で来た場合の両対応
                if isinstance(extracted_data, dict):
                    final_list = extracted_data.get("items", [])
                    # itemsがなくて直下にキーがある場合
                    if not final_list and "product_name" in extracted_data:
                        final_list = [extracted_data]
                elif isinstance(extracted_data, list):
                    final_list = extracted_data
                else:
                    final_list = []

                if final_list:
                    result_info["success"] = True
                    result_info["data"] = final_list
                    return result_info
                else:
                    # 空のJSONが返ってきた場合
                    result_info["error"] = "AIがデータを検出できませんでした"
            
            else:
                result_info["error"] = "JSON変換に失敗しました"

        except Exception as e:
            error_msg = str(e)
            result_info["error"] = error_msg
            if "429" in error_msg:
                time.sleep(5 * (attempt + 1))
                continue
            else:
                time.sleep(1)
                continue
            
    return result_info

# ==========================================
# 4. メイン処理（バッチ並列実行）
# ==========================================
st.title("⚡ AI高速・完全読み取り (Robust Ver)")
st.markdown("データの取りこぼしを防ぐ強力な抽出モードで実行します。")

uploaded_files = st.file_uploader(
    "ファイルをドラッグ＆ドロップ", 
    type=["pdf", "jpg", "png"], 
    accept_multiple_files=True
)

if uploaded_files:
    if st.button(f"高速読み取り開始 🚀", use_container_width=True):
        
        all_rows = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        error_log = []
        
        # --- PDFの前処理 ---
        all_tasks = []
        
        status_text.text("準備中: ページを展開しています...")
        
        for file in uploaded_files:
            if file.type == "application/pdf":
                try:
                    pdf_reader = PdfReader(file)
                    for i, page in enumerate(pdf_reader.pages):
                        # メモリ対策：データそのものはここでは持たず、参照だけ持つ
                        all_tasks.append({
                            "file_obj": file,
                            "page_index": i,
                            "label": f"{file.name} (p{i+1})",
                            "type": "pdf"
                        })
                except:
                    error_log.append(f"{file.name} 読み込み失敗")
            else:
                all_tasks.append({
                    "file_obj": file,
                    "label": file.name,
                    "type": "image"
                })

        total_tasks = len(all_tasks)
        st.write(f"合計 {total_tasks} ページを処理します。")

        # --- バッチ処理設定 ---
        BATCH_SIZE = 5  # 5並列
        
        for i in range(0, total_tasks, BATCH_SIZE):
            current_batch = all_tasks[i : i + BATCH_SIZE]
            batch_futures = {}
            
            status_text.text(f"🔥 高速処理中... {i+1}〜{min(i+BATCH_SIZE, total_tasks)} / {total_tasks} ページ")
            
            with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
                for task in current_batch:
                    # ここで初めてバイナリデータを生成（メモリ節約）
                    task_data = None
                    if task["type"] == "pdf":
                        reader = PdfReader(task["file_obj"])
                        writer = PdfWriter()
                        writer.add_page(reader.pages[task["page_index"]])
                        with io.BytesIO() as output:
                            writer.write(output)
                            task_data = output.getvalue()
                    else:
                        task_data = Image.open(task["file_obj"])

                    future = executor.submit(analyze_page_task, task_data, task["label"])
                    batch_futures[future] = task["label"]

                for future in as_completed(batch_futures):
                    label = batch_futures[future]
                    try:
                        result = future.result() # ここで result_info 辞書が返る
                        
                        if result["success"]:
                            items = result["data"]
                            for item in items:
                                row = {
                                    "ページ": label,
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
                            # エラー詳細をログに残す
                            error_reason = result.get("error", "不明なエラー")
                            error_log.append(f"{label} - {error_reason}")
                            
                    except Exception as e:
                        error_log.append(f"{label} - システムエラー: {e}")

            progress_bar.progress(min((i + BATCH_SIZE) / total_tasks, 1.0))
            gc.collect() 
            time.sleep(1)

        status_text.success("🎉 完了しました！")

        # 結果表示
        if error_log:
            with st.expander(f"⚠️ 読み取れなかった箇所 ({len(error_log)}件)"):
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
                file_name="robust_data.csv",
                mime="text/csv"
            )
        else:
            st.error("データを1件も抽出できませんでした。")
