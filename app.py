import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd
import time

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="AI一括伝票読み取り（完全版）", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。StreamlitのSecretsを設定してください。")
    st.stop()

# ==========================================
# 2. 解析を行う関数（修正版）
# ==========================================
def analyze_document_safe(input_data, mime_type):
    genai.configure(api_key=GOOGLE_API_KEY)
    
    # 【重要】あなたの環境で確実に動くモデル名に修正しました
    model_name = "gemini-flash-latest" 

    prompt = """
    以下のレシート・納品書・請求書を読み取り、純粋なJSON形式のみを出力してください。
    Markdown記法（```json 等）は含めないでください。
    
    【全体情報】
    - date (日付: YYYY-MM-DD)
    - company_name (仕入先・店名)
    - total_amount (伝票合計金額: 数値のみ)
    - invoice_number (インボイス番号)
    
    【明細リスト (items)】
    - jan_code (JAN/品番)
    - product_name (商品名)
    - quantity (数量: 数値)
    - retail_price (上代/定価: 数値)
    - cost_price (単価/下代: 数値)
    - line_total (金額/行合計: 数値)
    - wholesale_rate (掛け率)
    """
    
    # リトライ回数（エラーが出たら3回まで粘る）
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            model = genai.GenerativeModel(model_name)
            
            # PDFと画像で処理を分ける
            if mime_type == "application/pdf":
                content_part = {"mime_type": "application/pdf", "data": input_data}
                response = model.generate_content([prompt, content_part], request_options={"timeout": 600})
            else:
                response = model.generate_content([prompt, input_data], request_options={"timeout": 600})

            text = response.text
            # JSONをきれいに取り出す処理
            cleaned_text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(cleaned_text)

        except Exception as e:
            error_msg = str(e)
            # 「429 (混雑)」や「503 (サーバーダウン)」なら待って再開
            if "429" in error_msg or "503" in error_msg:
                wait_time = 10 * (attempt + 1) # 10秒、20秒、30秒と待つ時間を増やす
                time.sleep(wait_time)
                continue # もう一回トライ！
            elif "404" in error_msg:
                 # モデル名が間違っている場合
                 st.error(f"モデルが見つかりません。コード内の model_name を確認してください。")
                 return None
            else:
                # それ以外のエラーなら今回は諦める
                return None
    
    return None # 3回やってもダメなら諦める

# ==========================================
# 3. 画面のデザイン
# ==========================================
st.title("📂 AI伝票一括読み取りシステム")
st.markdown(f"設定モデル: `gemini-flash-latest` (PDF/画像 対応)")

uploaded_files = st.file_uploader
