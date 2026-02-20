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
st.set_page_config(page_title="AI爆速読み取り(有料テスト版)", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. 執念のJSON抽出関数
# ==========================================
def extract_json_force(text):
    text = text.strip()
    text = re.sub(r"^```json", "", text)
    text = re.sub(r"^```", "", text)
    text = re.sub(r"```$", "", text)
    text = text.strip()
    
    try:
        return json.loads(text)
    except:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            try: return json.loads(match.group(0))
            except: pass
            
    try:
        if not text.endswith("}"): text += "}]}"
        return json.loads(text)
    except: pass
    return None

# ==========================================
# 3. 解析関数（1ページ単体・高速リトライ型）
# ==========================================
def analyze_page(page_bytes, label):
    genai.configure(api_key=GOOGLE_API_KEY)
    model_name = "gemini-flash-latest" 
    
    prompt = """
    この伝票画像（1ページのみ）の「明細行」を全て読み取り、以下のJSON形式で出力してください。
    解説やMarkdownは一切不要です。
    [
      {
        "date": "日付", "company_name": "仕入先", "product_name": "商品名",
        "quantity": "数量(数値)", "cost_price": "単価(数値)", "line_total": "金額(数値)", "invoice_number": "インボイスNo"
      }
    ]
    """
    
    for attempt in range(3):
        try:
            model = genai.GenerativeModel(model_name)
            content_part = {"mime_type": "application/pdf", "data": page_bytes}
            response = model.generate_content(
                [prompt, content_part],
                generation_config={"response_mime_type": "application/json"}
            )
            
            data = extract_json_force(response.text)
            if data:
                if isinstance(data, dict) and "items" in data: return {"status": "success", "data": data["items"], "label": label}
                elif isinstance(data, list): return {"status": "success", "data": data, "label": label}
                else: return {"status": "success", "data": [data], "label": label}
            
            return {"status": "parse_error", "raw": response.text[:200], "label": label}
            
        except Exception as e:
            time.sleep(2)
            continue
            
    return {"status": "api_error", "raw": "通信失敗", "label": label}

# ==========================================
# 4. メイン処理（範囲指定・5並列）
# ==========================================
st.title("⚡ AI爆速読み取り (有料枠テスト版)")
st.markdown("有料枠のパワーを試すため、まずは**指定したページ範囲（例：1〜10ページ）**だけを5並列で高速処理します。")

uploaded_file = st.file_uploader("PDFファイルをアップロードしてください", type=["pdf"])

if uploaded_file:
    try:
        pdf_reader = PdfReader(uploaded_file)
        total_pages = len(pdf_reader.pages)
        
        st.success(f"📄 ファイル読み込み成功: 全 {total_pages} ページ")
        
        # --- ページ範囲指定UI ---
        col1, col2 = st.columns(2)
        with col1:
            start_p = st.number_input("開始ページ", min_value=1, max_value=total_pages, value=1)
        with col2:
            default_end = min(start_p + 9, total_pages) # デフォルトで10ページ分をセット
            end_p = st.number_input("終了ページ", min_value=start_p, max_value=total_pages, value=default_end)
            
        if st.button(f"🚀 指定範囲（{start_p}〜{end_p}ページ）でテスト実行", use_container_width=True):
            
            all_rows = []
            error_log = []
            target_pages = end_p - start_p + 1
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            status_text.text(f"🚀 処理準備中... {start_p}〜{end_p} ページを展開します")
            
            # 処理タスクの準備（指定範囲のみ）
            tasks = []
            for page_num in range(start_p, end_p + 1):
                page_idx = page_num - 1 
                pdf_writer = PdfWriter()
                pdf_writer.add_page(pdf_reader.pages[page_idx])
                with io.BytesIO() as output:
                    pdf_writer.write(output)
                    tasks.append({"bytes": output.getvalue(), "label": f"p{page_num}"})
            
            # --- フルパワー並列処理（5並列） ---
            completed_count = 0
            
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_task = {executor.submit(analyze_page, t["bytes"], t["label"]): t for t in tasks}
                
                for future in as_completed(future_to_task):
                    completed_count += 1
                    result = future.result()
                    label = result["label"]
                    
                    status_text.text(f"⚡ 爆速処理中... {completed_count}/{target_pages} ページ完了 ({label})")
                    progress_bar.progress(completed_count / target_pages)
                    
                    if result["status"] == "success" and result["data"]:
                        for item in result["data"]:
                            if isinstance(item, dict):
                                item["ページ"] = label
                                all_rows.append(item)
                    else:
                        raw_data = result.get("raw", "理由不明")
                        error_log.append(f"{label} - 読み取り失敗 ({raw_data})")
                    
                    gc.collect()
            
            status_text.success(f"🎉 完璧です！{start_p}〜{end_p}ページの処理が完了しました！")
            
            if error_log:
                with st.expander(f"⚠️ 一部読み取れなかった箇所 ({len(error_log)}件)"):
                    for err in error_log:
                        st.write(err)
            
            if all_rows:
                df = pd.DataFrame(all_rows)
                
                # ページ番号順に並び替え
                try:
                    df['sort_key'] = df['ページ'].str.replace('p', '').astype(int)
                    df = df.sort_values('sort_key').drop('sort_key', axis=1)
                except:
                    pass
                
                cols = ["ページ", "date", "company_name", "jan_code", "product_name", "quantity", "cost_price", "line_total", "invoice_number"]
                col_map = {"date":"日付", "company_name":"仕入先", "jan_code":"JAN", "product_name":"商品名", "quantity":"数量", "cost_price":"単価", "line_total":"金額", "invoice_number":"インボイス"}
                
                valid_cols = [c for c in cols if c in df.columns]
                df = df[valid_cols].rename(columns=col_map)
                
                st.subheader(f"📊 抽出データ ({len(df)}行)")
                st.dataframe(df)
                
                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label=f"CSVを保存（{start_p}〜{end_p}P） 💾", 
                    data=csv, 
                    file_name=f"test_data_p{start_p}-{end_p}.csv", 
                    mime="text/csv"
                )
            else:
                st.error("データを抽出できませんでした。")
                
    except Exception as e:
        st.error(f"システムエラー: {e}")
