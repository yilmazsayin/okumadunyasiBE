from flask import Flask, request, jsonify
from flask_cors import CORS
import docx

# Flask uygulamasını başlat
app = Flask(__name__)
# Frontend ile Backend'in farklı portlarda çalışmasından kaynaklanacak CORS hatasını engelle
CORS(app)

# '/upload' adresine bir dosya gönderildiğinde bu fonksiyon çalışacak
@app.route('/upload', methods=['POST'])
def upload_file():
    # Dosyanın request içinde olup olmadığını kontrol et
    if 'file' not in request.files:
        return jsonify({"error": "Dosya bulunamadı"}), 400
    
    file = request.files['file']
    
    # Dosya adının boş olup olmadığını kontrol et
    if file.filename == '':
        return jsonify({"error": "Dosya seçilmedi"}), 400

    # Eğer dosya varsa ve uzantısı .docx ise
    if file and file.filename.endswith('.docx'):
        try:
            # Word dosyasını oku
            doc = docx.Document(file)
            # Dosyanın içindeki tüm paragrafları birleştirerek metni oluştur
            full_text = "\n".join([para.text for para in doc.paragraphs])
            # Ayıklanan metni JSON formatında Frontend'e geri gönder
            return jsonify({"text": full_text})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
            
    return jsonify({"error": "Geçersiz dosya formatı. Lütfen .docx dosyası yükleyin."}), 400

# Sunucuyu başlatmak için
if __name__ == '__main__':
    # Debug modu, kodda değişiklik yapınca sunucunun yeniden başlamasını sağlar
    app.run(debug=True)