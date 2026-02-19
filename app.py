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
st.set_page_config(page_title="AI爆速読み取り (5ページ束ね方式)", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. 強力なJSON抽出関数
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
            try:
                return json.loads(match.group(0))
            except:
                pass
    return None

# ==========================================
# 3. 解析関数（5ページ一括処理用）
# ==========================================
def analyze_chunk(chunk_bytes):
    genai.configure(api_key=GOOGLE_API_KEY)
    # ★ 指定通りモデルを完全固定
    model_name = "gemini-flash-latest" 
    
    prompt = """
    この伝票画像（複数ページ）の「明細行」を全て読み取り、1つのJSONリストにまとめて出力してください。
    解説やMarkdownは一切不要です。必ず [ ] で囲まれたリスト形式にしてください。
    
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
            content_part = {"mime_type": "application/pdf", "data": chunk_bytes}
            
            # JSON出力をAIに強制する
            response = model.generate_content(
                [prompt, content_part],
                generation_config={"response_mime_type": "application/json"}
            )
            
            data = extract_json_force(response.text)
            
            if data:
                if isinstance(data, dict) and "items" in data:
                    return data["items"]
                elif isinstance(data, list):
                    return data
                else:
                    return [data]
            return []
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg:
                # 万が一制限に掛かっても10秒で復帰
                time.sleep(10)
                continue
            elif "404" in error_msg:
                return "MODEL_ERROR"
            else:
                time.sleep(2)
                continue
                
    return None

# ==========================================
# 4. メイン処理（スマート・バッチ処理）
# ==========================================
st.title("⚡ AI爆速読み取りシステム (最適化版)")
st.markdown("モデル:`gemini-flash-latest` / 制限回避のため**5ページずつ束ねて**高速処理します。")

uploaded_files = st.file_uploader(
    "PDFファイルをドラッグ＆ドロップ", 
    type=["pdf"], 
    accept_multiple_files=True
)

if uploaded_files:
    if st.button(f"高速一括読み取り開始 🚀", use_container_width=True):
        
        all_rows = []
        error_log = []
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for file in uploaded_files:
            try:
                pdf_reader = PdfReader(file)
                total_pages = len(pdf_reader.pages)
                
                # 5ページずつ処理する設定
                CHUNK_SIZE = 5
                
                for i in range(0, total_pages, CHUNK_SIZE):
                    end_page = min(i + CHUNK_SIZE, total_pages)
                    label = f"p{i+1}〜{end_page}"
                    
                    status_text.text(f"🔥 処理中: {file.name} - {label} ({end_page}/{total_pages} ページ完了)")
                    
                    # 5ページ分のPDFデータを作る
                    pdf_writer = PdfWriter()
                    for p in range(i, end_page):
                        pdf_writer.add_page(pdf_reader.pages[p])
                        
                    with io.BytesIO() as output:
                        pdf_writer.write(output)
                        chunk_bytes = output.getvalue()
                    
                    # AI解析の実行
                    result = analyze_chunk(chunk_bytes)
                    
                    if result == "MODEL_ERROR":
                        st.error("モデル名エラー: `gemini-flash-latest` が使用できません。")
                        st.stop()
                    elif result is not None:
                        for item in result:
                            if isinstance(item, dict):
                                item["ページ(目安)"] = label
                                all_rows.append(item)
                    else:
                        error_log.append(f"{label} - 解析失敗またはデータなし")
                    
                    # 進捗バーの更新
                    progress_bar.progress(end_page / total_pages)
                    
                    # メモリの掃除
                    del chunk_bytes
                    del pdf_writer
                    gc.collect()
                    
                    # ★超重要★ 
                    # 1分間15回の制限を回避するため、必ず4.5秒休む
                    # 待つように見えて、これが一番最速で終わる設定です。
                    time.sleep(4.5) 
                    
            except Exception as e:
                st.error(f"ファイル処理エラー: {e}")

        status_text.success("🎉 全ページの処理が完了しました！")

        # エラー表示
        if error_log:
            with st.expander(f"⚠️ 一部読み取れなかった箇所 ({len(error_log)}件)"):
                for err in error_log:
                    st.write(err)
            
        # 結果の出力
        if all_rows:
            df = pd.DataFrame(all_rows)
            
            # 列の整理
            cols = ["ページ(目安)", "date", "company_name", "jan_code", "product_name", "quantity", "cost_price", "line_total", "invoice_number"]
            col_map = {"date":"日付", "company_name":"仕入先", "jan_code":"JAN", "product_name":"商品名", "quantity":"数量", "cost_price":"単価", "line_total":"金額", "invoice_number":"インボイス"}
            
            valid_cols = [c for c in cols if c in df.columns]
            df = df[valid_cols].rename(columns=col_map)
            
            st.subheader(f"📊 抽出データ ({len(df)}行)")
            st.dataframe(df)
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSV保存 💾", csv, "fast_completed_data.csv", "text/csv")
        else:
            st.error("データを抽出できませんでした。")
