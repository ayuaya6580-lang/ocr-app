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
st.set_page_config(page_title="AI確実読み取り(無料枠リミット回避版)", layout="wide")

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
            try: return json.loads(match.group(0))
            except: pass
    try:
        if not text.endswith("}"): text += "}]}"
        return json.loads(text)
    except: pass
    return None

def analyze_page(page_bytes):
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
    
    # 万が一制限に掛かっても、60秒待って5回まで粘る
    for attempt in range(5):
        try:
            model = genai.GenerativeModel(model_name)
            content_part = {"mime_type": "application/pdf", "data": page_bytes}
            response = model.generate_content(
                [prompt, content_part],
                generation_config={"response_mime_type": "application/json"}
            )
            
            data = extract_json_force(response.text)
            if data:
                if isinstance(data, dict) and "items" in data: return {"status": "success", "data": data["items"]}
                elif isinstance(data, list): return {"status": "success", "data": data}
                else: return {"status": "success", "data": [data]}
            
            return {"status": "parse_error", "raw": response.text[:200]}
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg:
                # Googleが「50秒待て」と言っているので、確実に60秒休む
                time.sleep(60)
                continue
            else:
                time.sleep(5)
                continue
            
    return {"status": "api_error", "raw": "API通信失敗（制限エラーが継続しました）"}

# ==========================================
# 画面デザイン・メイン処理
# ==========================================
st.title("🛡️ AI確実読み取り (無料枠リミット回避版)")
st.markdown("Google AIの「1分間に15回まで」という無料枠の制限を超えないよう、**1ページごとに必ず5秒休憩**しながら確実に行進します。")

uploaded_file = st.file_uploader("PDFファイルを1つアップロードしてください", type=["pdf"])

if uploaded_file:
    try:
        pdf_reader = PdfReader(uploaded_file)
        total_pages = len(pdf_reader.pages)
        st.success(f"📄 ファイル読み込み成功: 全 {total_pages} ページ")
        
        # メモリあふれ防止のため、30〜50ページずつの分割処理を推奨
        col1, col2 = st.columns(2)
        with col1:
            start_p = st.number_input("開始ページ", min_value=1, max_value=total_pages, value=1)
        with col2:
            default_end = min(start_p + 29, total_pages) 
            end_p = st.number_input("終了ページ", min_value=start_p, max_value=total_pages, value=default_end)
            
        if st.button(f"指定範囲（{start_p}〜{end_p}ページ）の読み取り開始 🚀", use_container_width=True):
            
            all_rows = []
            error_log = []
            target_pages = end_p - start_p + 1
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for i, page_num in enumerate(range(start_p, end_p + 1)):
                page_idx = page_num - 1 
                label = f"p{page_num}"
                
                status_text.text(f"⏳ 処理中... {label} ({i+1}/{target_pages} ページ目)")
                
                pdf_writer = PdfWriter()
                pdf_writer.add_page(pdf_reader.pages[page_idx])
                
                with io.BytesIO() as output:
                    pdf_writer.write(output)
                    page_bytes = output.getvalue()
                
                result = analyze_page(page_bytes)
                
                if result["status"] == "success" and result["data"]:
                    for item in result["data"]:
                        if isinstance(item, dict):
                            item["ページ"] = label
                            all_rows.append(item)
                else:
                    raw_data = result.get("raw", "理由不明")
                    error_log.append(f"{label} - {raw_data}")
                
                progress_bar.progress((i + 1) / target_pages)
                del page_bytes
                del pdf_writer
                gc.collect()
                
                # ★ 超重要ポイント ★
                # 無料枠の制限（1分間に15回）を超えないため、必ず5秒待機する！
                # 60秒 ÷ 5秒 = 12回/分 なので、絶対に制限に引っかかりません。
                if i < target_pages - 1: # 最後のページ以外は休む
                    status_text.text(f"☕ 休憩中... (Google API制限回避のため5秒待機中)")
                    time.sleep(5)
                
            status_text.success(f"🎉 処理が完了しました！")
            
            if error_log:
                with st.expander(f"⚠️ エラー詳細 ({len(error_log)}件)"):
                    for err in error_log:
                        st.write(err)
            
            if all_rows:
                df = pd.DataFrame(all_rows)
                cols = ["ページ", "date", "company_name", "jan_code", "product_name", "quantity", "cost_price", "line_total", "invoice_number"]
                col_map = {"date":"日付", "company_name":"仕入先", "jan_code":"JAN", "product_name":"商品名", "quantity":"数量", "cost_price":"単価", "line_total":"金額", "invoice_number":"インボイス"}
                
                valid_cols = [c for c in cols if c in df.columns]
                df = df[valid_cols].rename(columns=col_map)
                
                st.subheader(f"📊 抽出データ ({len(df)}行)")
                st.dataframe(df)
                
                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label=f"CSV保存（{start_p}〜{end_p}P） 💾", 
                    data=csv, 
                    file_name=f"data_p{start_p}-{end_p}.csv", 
                    mime="text/csv"
                )
            else:
                st.warning("データが抽出できませんでした。")
                
    except Exception as e:
        st.error(f"ファイル読み込みエラー: {e}")
