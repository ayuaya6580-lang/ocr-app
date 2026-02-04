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
# 2. 解析を行う関数（PDF対応版）
# ==========================================
def analyze_document(input_data, mime_type):
    genai.configure(api_key=GOOGLE_API_KEY)
    
    # PDFも扱えるモデルを使用
    model_name = "gemini-1.5-flash" 

    prompt = """
    以下のレシート・納品書・請求書（画像またはPDF）を読み取り、純粋なJSON形式のみを出力してください。
    Markdown記法は含めないでください。
    
    【全体情報】
    - date (日付: YYYY-MM-DD)
    - company_name (仕入先・店名)
    - total_amount (伝票全体の合計金額: 税込み等の最終合計。数値のみ)
    - invoice_number (インボイス番号: T+数字13桁など。なければnull)
    
    【明細リスト (items)】
    各商品行について、以下の情報を抽出してください。
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
        
        with st.spinner(f"AIが書類を解析中... ({mime_type})"):
            # 画像とPDFでデータの渡し方が少し異なります
            if mime_type == "application/pdf":
                # PDFの場合は辞書形式で渡す
                content_part = {
                    "mime_type": "application/pdf",
                    "data": input_data
                }
                response = model.generate_content([prompt, content_part], request_options={"timeout": 600})
            else:
                # 画像の場合は今まで通り
                response = model.generate_content([prompt, input_data], request_options={"timeout": 600})

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
st.title("📦 AI仕入れ・経費読み取りくん (PDF対応)")
st.markdown("レシート(画像)や請求書(PDF)から **JAN・上代・掛け率・単価・金額** を抽出します。")

col1, col2 = st.columns(2)

with col1:
    # PDFも許可するように設定を変更
    uploaded_file = st.file_uploader("書類をアップロード", type=["jpg", "png", "jpeg", "webp", "pdf"])
    
    target_data = None
    file_type = ""

    if uploaded_file:
        # ファイルの種類を判定
        if uploaded_file.type == "application/pdf":
            st.info("📄 PDFファイルが選択されました")
            # PDFの場合はバイトデータとして読み込む
            target_data = uploaded_file.getvalue()
            file_type = "application/pdf"
        else:
            # 画像の場合
            image = Image.open(uploaded_file)
            st.image(image, caption="アップロード画像", use_container_width=True)
            target_data = image
            file_type = "image"

with col2:
    if target_data is not None:
        if st.button("詳細読み取り開始 🚀", use_container_width=True):
            result_json = analyze_document(target_data, file_type)
            
            if result_json:
                st.success("読み取り完了！")
                
                # --- データ加工処理 ---
                header_info = {
                    "日付": result_json.get("date"),
                    "仕入先・店名": result_json.get("company_name"),
                    "インボイスNo": result_json.get("invoice_number"),
                    "【伝票合計金額】": result_json.get("total_amount"),
                }

                rows = []
                items = result_json.get("items", [])
                
                if items:
                    for item in items:
                        row = header_info.copy()
                        row.update({
                            "JAN/品番": item.get("jan_code"),
                            "商品名": item.get("product_name"),
                            "数量": item.get("quantity"),
                            "上代(定価)": item.get("retail_price"),
                            "掛け率": item.get("wholesale_rate"),
                            "単価(下代)": item.get("cost_price"),
                            "金額(行合計)": item.get("line_total")
                        })
                        rows.append(row)
                else:
                    row = header_info.copy()
                    row.update({"商品名": "（明細なし）"})
                    rows.append(row)

                df = pd.DataFrame(rows)
                
                desired_order = [
                    "日付", "仕入先・店名", "JAN/品番", "商品名", 
                    "数量", "上代(定価)", "掛け率", "単価(下代)", "金額(行合計)", 
                    "【伝票合計金額】", "インボイスNo"
                ]
                final_columns = [c for c in desired_order if c in df.columns]
                df = df[final_columns]
                
                st.subheader("解析結果")
                st.dataframe(df)
                
                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="CSVデータとして保存 💾",
                    data=csv,
                    file_name="document_data.csv",
                    mime="text/csv",
                )
