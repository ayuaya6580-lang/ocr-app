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
st.set_page_config(page_title="AI高速・完全読み取り(Pro)", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. 解析関数（1ページ単位・高速型）
# ==========================================
def analyze_page_task(page_data, page_label):
    genai.configure(api_key=GOOGLE_API_KEY)
    # 高速かつ精度の高いモデル
    model_name = "gemini-flash-latest" 

    prompt = """
    この伝票画像の**明細行のみ**を抽出し、以下のJSON形式で出力してください。
    余計な文字は一切含めないでください。
    
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
    
    # リトライは3回まで（短く粘る）
    for attempt in range(3):
        try:
            model = genai.GenerativeModel(model_name)
            
            content_part = {"mime_type": "application/pdf", "data": page_data}
            response = model.generate_content(
                [prompt, content_part],
                generation_config={"response_mime_type": "application/json"}
            )
            return json.loads(response.text)

        except Exception as e:
            if "429" in str(e): # 使いすぎエラーなら
                time.sleep(5) # 5秒だけ待って再開
                continue
            else:
                time.sleep(1)
                continue
            
    return None

# ==========================================
# 3. メイン処理（バッチ並列実行）
# ==========================================
st.title("⚡ AI高速・完全読み取りシステム (Batch Parallel)")
st.markdown("5ページずつ同時並行で処理し、**高速かつメモリ不足で落ちない**最適なバランスで実行します。")

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
        # 全てのページを「タスクリスト」に分解する
        all_tasks = []
        
        status_text.text("準備中: ページをスキャンしています...")
        
        for file in uploaded_files:
            if file.type == "application/pdf":
                try:
                    pdf_reader = PdfReader(file)
                    for i, page in enumerate(pdf_reader.pages):
                        # メモリ節約のため、ここではバイナリ化せず「どのページの何番目か」だけ記録
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
        st.write(f"合計 {total_tasks} ページを高速処理します。")

        # --- バッチ処理設定 ---
        BATCH_SIZE = 5  # 一度に処理する枚数（5枚同時）
        
        for i in range(0, total_tasks, BATCH_SIZE):
            # 今回処理するバッチ（束）を取り出す
            current_batch = all_tasks[i : i + BATCH_SIZE]
            batch_futures = {}
            
            status_text.text(f"🔥 高速処理中... {i+1}〜{min(i+BATCH_SIZE, total_tasks)} / {total_tasks} ページ")
            
            # --- 並列実行 ---
            with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
                for task in current_batch:
                    # 必要なデータだけここで生成（メモリ節約）
                    task_data = None
                    if task["type"] == "pdf":
                        reader = PdfReader(task["file_obj"])
                        writer = PdfWriter()
                        writer.add_page(reader.pages[task["page_index"]])
                        with io.BytesIO() as output:
                            writer.write(output)
                            task_data = output.getvalue()
                    else:
                        task_data = Image.open(task["file_obj"]) # 画像の場合

                    # 並列スレッドに投入
                    future = executor.submit(analyze_page_task, task_data, task["label"])
                    batch_futures[future] = task["label"]

                # --- 結果回収 ---
                for future in as_completed(batch_futures):
                    label = batch_futures[future]
                    try:
                        result = future.result()
                        if result and "items" in result:
                            items = result.get("items", [])
                            if items:
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
                            # AIは答えたが明細が無かった場合など
                            pass
                    except Exception as e:
                        error_log.append(f"{label} - エラー: {e}")

            # 進捗更新
            progress_bar.progress(min((i + BATCH_SIZE) / total_tasks, 1.0))
            
            # ★重要：バッチごとにメモリを強制開放
            gc.collect() 
            # API制限対策の微小な休憩（連続アクセス防止）
            time.sleep(1)

        # --- 完了処理 ---
        status_text.success("🎉 全ページの処理が完了しました！")

        if error_log:
            with st.expander(f"⚠️ 読み取れなかった箇所 ({len(error_log)}件)"):
                for err in error_log:
                    st.write(err)
            
        if all_rows:
            df = pd.DataFrame(all_rows)
            # 列整理
            cols = ["ページ", "日付", "仕入先", "JAN", "商品名", "数量", "単価", "金額", "掛け率", "インボイス"]
            valid_cols = [c for c in cols if c in df.columns]
            df = df[valid_cols]
            
            st.subheader(f"📊 抽出結果: {len(df)}行")
            st.dataframe(df)
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="CSVデータを保存 💾",
                data=csv,
                file_name="completed_data.csv",
                mime="text/csv"
            )
        else:
            st.warning("データが見つかりませんでした。")
