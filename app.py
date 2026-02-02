import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd
import time

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="カンタンAI経費精算", layout="wide")

# ★新しいAPIキーをここに貼ってください
GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]

# ==========================================
# 2. 解析を行う関数（名称指定・修正版）
# ==========================================
def analyze_image(img):
    genai.configure(api_key=GOOGLE_API_KEY)
    
    # 【重要】あなたのログにあった「確実に存在する名前」を使います
    # これなら404エラーにならず、2.0のような混雑も避けられます
    model_name = "gemini-flash-latest" 

    prompt = """
    以下のレシート画像を読み取り、純粋なJSON形式のみを出力してください。
    Markdown記法（```json など）は含めないでください。
    
    【抽出項目】
    - date (日付: YYYY-MM-DD)
    - company_name (店名・会社名)
    - total_amount (合計金額: 数値のみ)
    - invoice_number (インボイス番号: T+数字13桁など)
    - items (明細: 品名と金額)
    """
    
    try:
        model = genai.GenerativeModel(model_name)
        
        with st.spinner(f"AIが解析中... (使用モデル: {model_name})"):
            # 混雑回避のため、タイムアウト時間を長めに設定
            response = model.generate_content([prompt, img], request_options={"timeout": 600})
            text = response.text
            cleaned_text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(cleaned_text)
            
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            st.error("⚠️ 現在、Googleの無料枠が非常に混雑しています。時間を置いて（数時間後〜明日）試してください。")
        elif "404" in error_msg:
            st.error(f"⚠️ モデルが見つかりません。コード内の model_name を確認してください。詳細: {e}")
        else:
            st.error(f"エラーが発生しました: {e}")
        return None

# ==========================================
# 3. 画面のデザイン (UI)
# ==========================================
st.title("🧾 AIレシート読み取りくん (安定版)")
st.caption(f"現在の設定: {GOOGLE_API_KEY[:5]}... / モデル: gemini-flash-latest")

col1, col2 = st.columns(2)

with col1:
    uploaded_file = st.file_uploader("レシート画像をここにドラッグ＆ドロップ", type=["jpg", "png", "jpeg"])
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="アップロードされた画像", use_container_width=True)

with col2:
    if uploaded_file is not None:
        if st.button("読み取り開始 🚀", use_container_width=True):
            result_json = analyze_image(image)
            
            if result_json:
                st.success("読み取り完了！")
                st.subheader("読み取り結果")
                st.json(result_json)
                
                flat_data = {
                    "日付": result_json.get("date"),
                    "店名": result_json.get("company_name"),
                    "金額": result_json.get("total_amount"),
                    "インボイス番号": result_json.get("invoice_number")
                }
                df = pd.DataFrame([flat_data])
                
                st.subheader("データ確認")
                st.dataframe(df)
                
                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="CSV保存 💾",
                    data=csv,
                    file_name="receipt_data.csv",
                    mime="text/csv",
                )