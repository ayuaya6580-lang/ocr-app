import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
import time
from pypdf import PdfReader, PdfWriter
import io
import re
import gc

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="AI確実読み取り(分割処理版)", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

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
            try:
                return json.loads(match.group(0))
            except:
                pass
    
    try:
        if not text.endswith("}"):
            text += "}]}"
        return json.loads(text)
    except:
        pass
    return None

def analyze_page(page_bytes):
    genai.configure(api_key=GOOGLE_API_KEY)
    model_name = "gemini-flash-latest" 
    
    prompt = """
    この伝票画像（1ページのみ）の「明細行」を全て読み取り、以下のJSON形式で出力してください。
    解説やMarkdownは一切不要です。
    
    [
      {
        "date": "日付",
        "company_name": "仕入先",
        "product_name": "商品名",
        "quantity": "数量(数値)",
        "cost_price": "単価(数値)",
        "line_total": "金額(数値)",
        "invoice_number": "インボイスNo"
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
                if isinstance(data, dict) and "items" in data:
                    return {"status": "success", "data": data["items"]}
                elif isinstance(data, list):
                    return {"status": "success", "data": data}
                else:
                    return {"status": "success", "data": [data]}
            
            return {"status": "parse_error", "raw": response.text[:200]}
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg:
                time.sleep(10)
                continue
            else:
                time.sleep(2)
                continue
    return {"status": "api_error", "raw": "API通信失敗"}

# ==========================================
# 画面デザイン・メイン処理
# ==========================================
st.title("🛡️ AI確実読み取り (範囲指定・分割処理版)")
st.markdown("長時間の連続処理による**システムの強制終了（初期画面に戻る現象）**を防ぐため、数十ページずつ範囲を指定して確実に処理します。")

# アップロード枠（分かりやすく1ファイル限定に変更）
uploaded_file = st.file_uploader("PDFファイルを1つアップロードしてください", type=["pdf"])

if uploaded_file:
    try:
        pdf_reader = PdfReader(uploaded_file)
        total_pages = len(pdf_reader.pages)
        
        st.success(f"📄 ファイル読み込み成功: 全 {total_pages} ページ")
        st.info("💡 135ページ等の大容量ファイルは、30ページずつ分けて処理することで、途中で落ちずに確実にデータ化できます。")
        
        # --- ページ範囲指定UI ---
        col1, col2 = st.columns(2)
        with col1:
            start_p = st.number_input("開始ページ", min_value=1, max_value=total_pages, value=1)
        with col2:
            default_end = min(start_p + 29, total_pages) # デフォルトで30ページ分をセット
            end_p = st.number_input("終了ページ", min_value=start_p, max_value=total_pages, value=default_end)
            
        if st.button(f"指定範囲（{start_p}ページ 〜 {end_p}ページ）の読み取り開始 🚀", use_container_width=True):
            
            all_rows = []
            error_log = []
            
            target_pages = end_p - start_p + 1
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for i, page_num in enumerate(range(start_p, end_p + 1)):
                page_idx = page_num - 1 # プログラムは0から数えるため
                label = f"p{page_num}"
                
                status_text.text(f"⏳ 処理中... {label} ({i+1}/{target_pages} ページ目)")
                
                # 1ページ切り出し
                pdf_writer = PdfWriter()
                pdf_writer.add_page(pdf_reader.pages[page_idx])
                
                with io.BytesIO() as output:
                    pdf_writer.write(output)
                    page_bytes = output.getvalue()
                
                # 解析
                result = analyze_page(page_bytes)
                
                if result["status"] == "success" and result["data"]:
                    for item in result["data"]:
                        if isinstance(item, dict):
                            item["ページ"] = label
                            all_rows.append(item)
                else:
                    raw_data = result.get("raw", "理由不明")
                    error_log.append(f"{label} - 読み取り失敗 ({raw_data})")
                
                # 進捗更新とメモリ解放
                progress_bar.progress((i + 1) / target_pages)
                del page_bytes
                del pdf_writer
                gc.collect()
                
                # API制限対策
                time.sleep(3)
                
            status_text.success(f"🎉 {start_p}〜{end_p}ページの処理が完了しました！")
            
            if error_log:
                with st.expander(f"⚠️ 一部読み取れなかった箇所 ({len(error_log)}件)"):
                    for err in error_log:
                        st.write(err)
            
            # --- 結果表示とCSVダウンロード ---
            if all_rows:
                df = pd.DataFrame(all_rows)
                cols = ["ページ", "date", "company_name", "jan_code", "product_name", "quantity", "cost_price", "line_total", "invoice_number"]
                col_map = {"date":"日付", "company_name":"仕入先", "jan_code":"JAN", "product_name":"商品名", "quantity":"数量", "cost_price":"単価", "line_total":"金額", "invoice_number":"インボイス"}
                
                valid_cols = [c for c in cols if c in df.columns]
                df = df[valid_cols].rename(columns=col_map)
                
                st.subheader(f"📊 抽出データ ({len(df)}行)")
                st.dataframe(df)
                
                csv = df.to_csv(index=False).encode('utf-8-sig')
                # ファイル名にページ番号を入れて保存
                st.download_button(
                    label=f"CSV保存（{start_p}〜{end_p}P） 💾", 
                    data=csv, 
                    file_name=f"data_p{start_p}-{end_p}.csv", 
                    mime="text/csv"
                )
            else:
                st.error("データを抽出できませんでした。")
                
    except Exception as e:
        st.error(f"ファイル読み込みエラー: {e}")
