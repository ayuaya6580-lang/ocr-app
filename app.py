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
st.set_page_config(page_title="AI高速・完全読み取り(Final)", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. 頑丈なJSON抽出関数
# ==========================================
def extract_json_force(text):
    """
    AIの返答からJSONデータだけを無理やり抜き出す
    """
    text = text.strip()
    # Markdown削除
    text = text.replace("```json", "").replace("```", "")
    
    # パターン1: 単純なJSON変換
    try:
        return json.loads(text)
    except:
        pass

    # パターン2: リスト [...] または 辞書 {...} を正規表現で探す
    try:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except:
        pass
        
    return None

# ==========================================
# 3. 解析関数（1ページ単位）
# ==========================================
def analyze_page_task(page_bytes, page_label):
    genai.configure(api_key=GOOGLE_API_KEY)
    
    # ★★★ 最重要修正箇所 ★★★
    # あなたの環境で確実に動くモデル名に戻しました
    model_name = "gemini-flash-latest" 

    prompt = """
    この画像の「明細行」を全て読み取り、以下のJSONリスト形式で出力してください。
    余計な挨拶や解説は一切不要です。
    
    [
      {
        "date": "日付",
        "company_name": "仕入先名",
        "product_name": "商品名",
        "quantity": "数量(数値)",
        "cost_price": "単価(数値)",
        "line_total": "金額(数値)",
        "invoice_number": "インボイスNo"
      }
    ]
    """
    
    # リトライ処理
    for attempt in range(3):
        try:
            model = genai.GenerativeModel(model_name)
            
            # PDFページデータを直接渡す
            content_part = {"mime_type": "application/pdf", "data": page_bytes}
            
            response = model.generate_content([prompt, content_part])
            
            # データ抽出
            data = extract_json_force(response.text)
            
            if data:
                # 辞書で返ってきたらリストに入れる
                if isinstance(data, dict):
                    if "items" in data:
                        return data["items"]
                    else:
                        return [data]
                elif isinstance(data, list):
                    return data
            
        except Exception as e:
            error_msg = str(e)
            # 429エラー(混雑)なら少し待つ
            if "429" in error_msg:
                time.sleep(5)
                continue
            # 404エラー(モデル違い)なら即停止(設定ミスのため)
            elif "404" in error_msg:
                return {"fatal_error": "モデル名エラー: コード内のmodel_nameを確認してください"}
            else:
                time.sleep(1)
                continue
            
    return None # 失敗

# ==========================================
# 4. メイン処理（並列実行）
# ==========================================
st.title("🚀 AI高速・完全読み取りシステム (Final Fix)")
st.markdown("モデル設定を修正しました。**3ページ同時並行**で高速処理します。")

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
        
        # --- 準備：タスク作成 ---
        tasks = []
        status_text.text("ページを準備中...")
        
        for file in uploaded_files:
            if file.type == "application/pdf":
                try:
                    pdf_reader = PdfReader(file)
                    for i, page in enumerate(pdf_reader.pages):
                        # メモリ節約のため、処理直前にバイト化する準備だけしておく
                        tasks.append({
                            "file_obj": file,
                            "page_index": i,
                            "label": f"{file.name} (p{i+1})",
                            "type": "pdf"
                        })
                except:
                    error_log.append(f"{file.name}: 読み込み失敗")
            else:
                # 画像
                tasks.append({
                    "file_obj": file,
                    "label": file.name,
                    "type": "image"
                })

        total_tasks = len(tasks)
        st.write(f"処理対象: 全 {total_tasks} ページ")

        # --- 並列実行（3並列） ---
        # 3並列なら速度と安定性のバランスが良いです
        with ThreadPoolExecutor(max_workers=3) as executor:
            
            # 未来の仕事を登録
            future_to_task = {}
            for t in tasks:
                # ここでデータをバイナリ化
                input_data = None
                if t["type"] == "pdf":
                    reader = PdfReader(t["file_obj"])
                    writer = PdfWriter()
                    writer.add_page(reader.pages[t["page_index"]])
                    with io.BytesIO() as output:
                        writer.write(output)
                        input_data = output.getvalue()
                else:
                    input_data = Image.open(t["file_obj"])
                
                # スレッドに投入
                future = executor.submit(analyze_page_task, input_data, t["label"])
                future_to_task[future] = t["label"]

            # 完了順に処理
            completed_count = 0
            for future in as_completed(future_to_task):
                label = future_to_task[future]
                completed_count += 1
                
                # 進捗表示
                status_text.text(f"処理中... {completed_count}/{total_tasks} : {label}")
                progress_bar.progress(completed_count / total_tasks)
                
                try:
                    result = future.result()
                    
                    if isinstance(result, list):
                        # 成功
                        for item in result:
                            # 辞書型であることを確認
                            if isinstance(item, dict):
                                item["ページ"] = label
                                all_rows.append(item)
                    elif isinstance(result, dict) and "fatal_error" in result:
                        st.error(f"重大エラー: {result['fatal_error']}")
                        break # 処理中断
                    else:
                        error_log.append(f"{label}: データ読み取り失敗")
                        
                except Exception as e:
                    error_log.append(f"{label}: システムエラー {e}")
                
                # メモリ解放
                gc.collect()

        status_text.success("✅ 完了しました！")

        # --- 結果表示 ---
        if error_log:
            with st.expander(f"⚠️ エラーログ ({len(error_log)}件)"):
                for err in error_log:
                    st.write(err)
            
        if all_rows:
            df = pd.DataFrame(all_rows)
            # 列の整理
            cols = ["ページ", "date", "company_name", "jan_code", "product_name", 
                    "quantity", "cost_price", "line_total", "invoice_number"]
            col_map = {
                "date": "日付", "company_name": "仕入先", "jan_code": "JAN", 
                "product_name": "商品名", "quantity": "数量", "cost_price": "単価", 
                "line_total": "金額", "invoice_number": "インボイス"
            }
            
            # 存在する列だけ残してリネーム
            existing_cols = [c for c in cols if c in df.columns]
            df = df[existing_cols].rename(columns=col_map)
            
            st.subheader(f"📊 抽出データ ({len(df)}行)")
            st.dataframe(df)
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSV保存 💾", csv, "final_data.csv", "text/csv")
        else:
            st.warning("データを抽出できませんでした。")
