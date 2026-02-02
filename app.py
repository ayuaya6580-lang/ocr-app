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

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("APIキーが設定されていません。StreamlitのSecretsを設定してください。")
    st.stop()

# ==========================================
# 2. 解析を行う関数（下代・金額対応版）
# ==========================================
def analyze_image(img):
    genai.configure(api_key=GOOGLE_API_KEY)
    
    # 安定して動くモデル（状況に応じて変更可）
    model_name = "gemini-flash-latest" 

    # ★ここが変更点：上代・下代・金額を明確に区別して指示します
    prompt = """
    以下のレシート・納品書・請求書画像を読み取り、純粋なJSON形式のみを出力してください。
    Markdown記法は含めないでください。
    
    【全体情報】
    - date (日付: YYYY-MM-DD)
    - company_name (仕入先・店名)
    - total_amount (伝票全体の合計金額: 税込み等の最終合計。数値のみ)
    - invoice_number (インボイス番号: T+数字13桁など。なければnull)
    
    【明細リスト (items)】
    画像内の各商品行について、以下の情報を抽出してください。
    特に「単価(下代)」と「上代」を混同しないように注意してください。
    
    - jan_code (JANコード/品番。なければnull)
    - product_name (商品名)
    - quantity (数量。数値のみ)
    - retail_price (上代/定価。記載がなければnull)
    - wholesale_rate (掛け率。例: 60, 0.6など。記載がなければnull)
    - cost_price (単価/下代/原単価。これが仕入れ単価になります。数値のみ)
    - line_total (金額/下代合計/行合計。単価×数量の結果。数値のみ)
    """
    
    try:
        model = genai.GenerativeModel(model_name)
        
        with st.spinner(f"AIが詳細解析中... (JAN・下代・金額など)"):
            response = model.generate_content([prompt, img], request_options={"timeout": 600})
            text = response.text
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
st.markdown("レシートや納品書から **JAN・上代・掛け率・単価(下代)・金額** を抽出します。")

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
                header_info = {
                    "日付": result_json.get("date"),
                    "仕入先・店名": result_json.get("company_name"),
                    "インボイスNo": result_json.get("invoice_number"),
                    "【伝票合計金額】": result_json.get("total_amount"), # 全体の合計
                }

                rows = []
                items = result_json.get("items", [])
                
                if items:
                    for item in items:
                        row = header_info.copy()
                        # ★ここが追加項目
                        row.update({
                            "JAN/品番": item.get("jan_code"),
                            "商品名": item.get("product_name"),
                            "数量": item.get("quantity"),
                            "上代(定価)": item.get("retail_price"),
                            "掛け率": item.get("wholesale_rate"),
                            "単価(下代)": item.get("cost_price"),  # 追加
                            "金額(行合計)": item.get("line_total")   # 追加
                        })
                        rows.append(row)
                else:
                    row = header_info.copy()
                    row.update({"商品名": "（明細なし）"})
                    rows.append(row)

                # 表の列の順番をきれいに並べ替え
                df = pd.DataFrame(rows)
                
                # 見やすい順序に並べ替え（列が存在する場合のみ）
                desired_order = [
                    "日付", "仕入先・店名", "JAN/品番", "商品名", 
                    "数量", "上代(定価)", "掛け率", "単価(下代)", "金額(行合計)", 
                    "【伝票合計金額】", "インボイスNo"
                ]
                # 実際にデータにある列だけを選んで並べる（エラー防止）
                final_columns = [c for c in desired_order if c in df.columns]
                df = df[final_columns]
                
                st.subheader("解析結果")
                st.dataframe(df)
                
                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="CSVデータとして保存 💾",
                    data=csv,
                    file_name="purchase_data_v2.csv",
                    mime="text/csv",
                )
