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
st.set_page_config(page_title="AI確実読み取り(1ページ最適化版)", layout="wide")

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
            try:
                return json.loads(match.group(0))
            except:
                pass
    
    # 途中で切れたデータを無理やり閉じて復旧を試みる
    try:
        if not text.endswith("}"):
            text += "}]}"
        return json.loads(text)
    except:
        pass
        
    return None

# ==========================================
# 3. 解析関数（1ページ単体・超安定型）
# ==========================================
def analyze_page(page_bytes):
    genai.configure(api_key=GOOGLE_API_KEY)
    model_name = "gemini-flash-latest" 
    
    prompt = """
    この伝票画像（1ページのみ）の「明細行」を全て読み取り、以下のJSON形式で出力してください。
    解説やMarkdownは一切不要です。
    
    {
      "items": [
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
    }
    """
    
    for attempt in range(3):
        try:
            model = genai.GenerativeModel(model_name)
            content_part = {"mime_type": "application/pdf", "data": page_bytes}
            
            # JSON出力強制 + 出力文字数を最大化
            response = model.generate_content(
                [prompt, content_part],
                generation_config={
                    "response_mime_type": "application/json",
                    "max_output_tokens": 8192
                }
            )
            
            data = extract_json_force(response.text)
            
            if data:
                if isinstance(data, dict) and "items" in data:
                    return {"status": "success", "data": data["items"]}
                elif isinstance(data, list):
                    return {"status": "success", "data": data}
                else:
                    return {"status": "success", "data": [data]}
            
            # データはあるがJSONにならなかった場合、原因を返す
            return {"status": "parse_error", "raw": response.text[:200]}
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg:
                time.sleep(10) # 制限に引っかかったら10秒待機
                continue
            else:
                time.sleep(2)
                continue
                
    return {"status": "api_error", "raw": "APIの通信に失敗しました"}

# ==========================================
# 4. メイン処理（メモリ節約・順次実行）
# ==========================================
st.title("🛡️ AI確実読み取りシステム (1ページ最適化版)")
st.markdown("文字数オーバーによるエラーを防ぐため、**1ページずつ確実にノンストップ**で処理します。")

uploaded_files = st.file_uploader(
    "PDFファイルをドラッグ＆ドロップ", 
    type=["pdf"], 
    accept_multiple_files=True
)

if uploaded_files:
    if st.button(f"一括読み取り開始 🚀", use_container_width=True):
        
        all_rows = []
        error_log = []
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for file in uploaded_files:
            try:
                pdf_reader = PdfReader(file)
                total_pages = len(pdf_reader.pages)
                
                st.write(f"📄 {file.name} (全 {total_pages} ページ) の処理を開始します。このままお待ちください...")
                
                # 1ページずつループ処理
                for i in range(total_pages):
                    label = f"p{i+1}"
                    status_text.text(f"⏳ 処理中... {label} / {total_pages} ページ目")
                    
                    # 1ページだけ切り出し（メモリを食わない）
                    pdf_writer = PdfWriter()
                    pdf_writer.add_page(pdf_reader.pages[i])
                    
                    with io.BytesIO() as output:
                        pdf_writer.write(output)
                        page_bytes = output.getvalue()
                    
                    # AI解析実行
                    result = analyze_page(page_bytes)
                    
                    # 結果の判定
                    if result["status"] == "success" and result["data"]:
                        for item in result["data"]:
                            if isinstance(item, dict):
                                item["ページ"] = label
                                all_rows.append(item)
                    else:
                        # 失敗の理由を記録
                        raw_data = result.get("raw", "理由不明")
                        error_log.append(f"{label} - 読み取り失敗 (AIの返答: {raw_data}...)")
                    
                    # 進捗の更新
                    progress_bar.progress((i + 1) / total_pages)
                    
                    # ★超重要：メモリ掃除とAPI制限回避のインターバル
                    del page_bytes
                    del pdf_writer
                    gc.collect()
                    
                    # 1分間に15回の制限を超えないための「4秒待機」
                    # （これが一番落ちずに早く終わるペースです）
                    time.sleep(4)
                    
            except Exception as e:
                st.error(f"ファイル処理エラー: {e}")

        status_text.success("🎉 すべての処理が完了しました！")

        # --- エラー詳細の表示 ---
        if error_log:
            with st.expander(f"⚠️ 一部読み取れなかった箇所 ({len(error_log)}件 - クリックして原因を確認)"):
                st.write("「AIの返答」に文字が入っている場合、AIは頑張って読んでいますが形式が崩れています。")
                for err in error_log:
                    st.write(err)
            
        # --- 結果表示 ---
        if all_rows:
            df = pd.DataFrame(all_rows)
            
            cols = ["ページ", "date", "company_name", "jan_code", "product_name", "quantity", "cost_price", "line_total", "invoice_number"]
            col_map = {"date":"日付", "company_name":"仕入先", "jan_code":"JAN", "product_name":"商品名", "quantity":"数量", "cost_price":"単価", "line_total":"金額", "invoice_number":"インボイス"}
            
            valid_cols = [c for c in cols if c in df.columns]
            df = df[valid_cols].rename(columns=col_map)
            
            st.subheader(f"📊 抽出データ ({len(df)}行)")
            st.dataframe(df)
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSV保存 💾", csv, "completed_data.csv", "text/csv")
        else:
            st.error("データを抽出できませんでした。")
