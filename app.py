import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd
import time

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="AI仕入れ・経費読み取り", layout="wide")

# クラウドの「Secrets（金庫）」からキーを読み込む設定
# ※手元で動かす場合は、ここに直接キーを書いても動きますが、公開時はsecretsを使います
try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("APIキーが設定されていません。StreamlitのSecretsを設定してください。")
    st.stop()

# ==========================================
# 2. 解析を行う関数（新項目対応版）
# ==========================================
def analyze_image(img):
    genai.configure(api_key=GOOGLE_API_KEY)
    
    # モデル設定（あなたの環境で動くもの）
    model_name = "gemini-flash-latest" 
    # もしエラーが出る場合は "gemini-2.0-flash" や "gemini-1.5-flash" に変更

    # ★ここが変更点：読み取る項目を詳細に指定します
    prompt = """
    以下のレシート・請求書画像を読み取り、純粋なJSON形式のみを出力してください。
    Markdown記法（```json 等）は含めないでください。
    
    【全体情報】
    - date (日付: YYYY-MM-DD)
    - company_name (仕入先・店名)
    - total_amount (合計金額: 数値のみ)
    - invoice_number (インボイス番号: T+数字13桁など。なければnull)
    
    【明細リスト (items)】
    画像内の各商品行について、以下の情報を抽出してください。
    - jan_code (JANコード/バーコード/品番。なければnull)
    - product_name (商品名)
    - retail_price (上代/定価/単価。数値のみ)
    - quantity (数量。数値のみ)
    - wholesale_rate (掛け率。例: "60", "0.6", "60%"など。記載がなければnull)
    """
    
    try:
        model = genai.GenerativeModel(model_name)
        
        with st.spinner(f"AIが詳細解析中... (JAN・掛け率・上代など)"):
            response = model.generate_content([prompt, img], request_options={"timeout": 600})
            text = response.text
            # JSON整形
            cleaned_text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(cleaned_text)
            
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            st.error("⚠️ 混雑のためエラーになりました。少し時間をおいて再試行してください。")
        else:
            st.error(f"エラーが発生しました: {e}")
        return None

# ==========================================
# 3. 画面のデザイン (UI)
# ==========================================
st.title("📦 AI仕入れ・経費読み取りくん")
st.markdown("レシートや請求書から、**JANコード・商品名・上代・掛け率・数量** を抽出します。")

col1, col2 = st.columns(2)

with col1:
    uploaded_file = st.file_uploader("画像をここにアップロード", type=["jpg", "png", "jpeg", "webp"])
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="アップロード画像", use_container_width=True)

with col2:
    if uploaded_file is not None:
        if st.button("詳細読み取り開始 🚀", use_container_width=True):
            result_json = analyze_image(image)
            
            if result_json:
                st.success("読み取り完了！")
                
                # --- データ加工処理 ---
                # ヘッダー情報（日付や店名）
                header_info = {
                    "日付": result_json.get("date"),
                    "仕入先・店名": result_json.get("company_name"),
                    "インボイスNo": result_json.get("invoice_number"),
                }

                # 明細行を作る（ここが重要！）
                rows = []
                items = result_json.get("items", [])
                
                if items:
                    for item in items:
                        # ヘッダー情報 + 商品情報を合体させた1行を作る
                        row = header_info.copy()
                        row.update({
                            "JANコード/品番": item.get("jan_code"),
                            "商品名": item.get("product_name"),
                            "数量": item.get("quantity"),
                            "上代(単価)": item.get("retail_price"),
                            "掛け率": item.get("wholesale_rate")
                        })
                        rows.append(row)
                else:
                    # 明細が取れなかった場合はヘッダー情報だけの行を作る
                    row = header_info.copy()
                    row.update({"商品名": "（明細なし）"})
                    rows.append(row)

                # 表形式に変換
                df = pd.DataFrame(rows)
                
                # 画面に表示
                st.subheader("解析結果")
                st.dataframe(df) # 表を表示
                
                # CSVダウンロードボタン
                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="CSVデータとして保存 💾",
                    data=csv,
                    file_name="purchase_data.csv",
                    mime="text/csv",
                )
                
                # JSONも確認用に表示（折りたたみ）
                with st.expander("元のJSONデータを見る"):
                    st.json(result_json)
