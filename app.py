import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd
import time
import re

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="AI一括伝票読み取り（診断モード）", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。Secretsを確認してください。")
    st.stop()

# ==========================================
# 2. 解析を行う関数（エラー内容を表示する版）
# ==========================================
def analyze_document_debug(input_data, mime_type):
    genai.configure(api_key=GOOGLE_API_KEY)
    
    # モデル設定（まずは安定版を指定）
    model_name = "gemini-1.5-flash"

    prompt = """
    あなたは経理アシスタントです。
    以下の画像を読み取り、純粋なJSONデータのみを返してください。
    解説やMarkdown（```json 等）は一切不要です。
    
    出力フォーマット:
    {
        "date": "YYYY-MM-DD",
        "company_name": "店名",
        "total_amount": "数値",
        "invoice_number": "T番号",
        "items": [
            {
                "product_name": "商品名",
                "quantity": "数量",
                "cost_price": "単価",
                "line_total": "金額"
            }
        ]
    }
    """
    
    model = genai.GenerativeModel(model_name)
    
    try:
        # 画像かPDFかでデータを準備
        if mime_type == "application/pdf":
            content_part = {"mime_type": "application/pdf", "data": input_data}
            response = model.generate_content([prompt, content_part])
        else:
            response = model.generate_content([prompt, input_data])

        text = response.text
        
        # --- ここから診断用ロジック ---
        # AIの生出力をデバッグ表示（開発者用）
        # print(f"AI Raw Output: {text}") 

        # JSONクリーニング（強力版）
        # ```json や ``` を削除
        cleaned_text = re.sub(r"```json|```", "", text).strip()
        
        # 波カッコ { } の範囲だけを無理やり抽出する（余計な文字対策）
        match = re.search(r"\{.*\}", cleaned_text, re.DOTALL)
        if match:
            cleaned_text = match.group(0)
        
        return json.loads(cleaned_text)

    except Exception as e:
        # ★ここでエラーの正体を画面に出す！
        st.error(f"⚠️ 詳細エラー: {e}")
        # もしJSON変換エラーなら、AIが何を言っていたかを表示
        if "Expecting value" in str(e) or "JSONDecodeError" in str(e):
             st.warning(f"AIの返答がJSONではありませんでした:\n{text}")
        return None

# ==========================================
# 3. 画面のデザイン
# ==========================================
st.title("🩺 AI一括読み取り（エラー診断モード）")
st.markdown("エラーの詳細を画面に表示します。")

uploaded_files = st.file_uploader(
    "ファイルをアップロードしてください", 
    type=["jpg", "png", "jpeg", "pdf"], 
    accept_multiple_files=True
)

if uploaded_files:
    file_count = len(uploaded_files)
    
    if st.button(f"診断読み取り開始 ({file_count}件) 🚀", use_container_width=True):
        
        all_rows = []
        progress_bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            st.write(f"🔍 解析中: {file.name} ...")
            
            # 休憩（API制限対策）
            time.sleep(2)

            try:
                file_bytes = file.getvalue()
                mime_type = "application/pdf" if file.type == "application/pdf" else "image"
                if mime_type == "image":
                    file_bytes = Image.open(file)
                
                result = analyze_document_debug(file_bytes, mime_type)
                
                if result:
                    st.success(f"✅ 成功: {file.name}")
                    # データ加工（簡易版）
                    header_info = {
                        "ファイル名": file.name,
                        "日付": result.get("date"),
                        "仕入先": result.get("company_name"),
                        "合計": result.get("total_amount")
                    }
                    items = result.get("items", [])
                    if items:
                        for item in items:
                            row = header_info.copy()
                            row.update(item)
                            all_rows.append(row)
                    else:
                        all_rows.append(header_info)
                else:
                    st.error(f"❌ 失敗: {file.name}（上記のエラー詳細を確認してください）")
            
            except Exception as e:
                st.error(f"❌ ファイル読み込みエラー: {file.name} / {e}")

            progress_bar.progress((i + 1) / file_count)

        # --- 結果表示 ---
        if all_rows:
            df = pd.DataFrame(all_rows)
            st.dataframe(df)
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("CSV保存", csv, "debug_data.csv", "text/csv")
        else:
            st.error("データを読み取れませんでした。")
