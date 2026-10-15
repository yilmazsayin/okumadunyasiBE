# Okuma Dünyası Backend V2

Flask API: DOCX ve seçilebilir metin içeren PDF belgelerini okuma pratiği için
metne dönüştürür. Mevcut `POST /upload` adresi, multipart `file` alanı ve JSON
sözleşmesi korunur. Frontend ve Render ayarları bu değişiklikte düzenlenmez.

## Kurulum ve çalıştırma

Linux ve Python 3.12 ile doğrulanmıştır. Kaynak sınırlı işçiler POSIX `resource`
ve `multiprocessing` forkserver kullanır; Windows'ta doğrudan çalıştırmak yerine
Linux/WSL kullanın.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 1 app:app
```

Mevcut `gunicorn app:app` giriş noktası korunur. Yerel geliştirmede `--reload`
eklenebilir. `python app.py` geliştirme sunucusunu debug kapalı başlatır.

```bash
curl -F 'file=@ornek.docx' http://127.0.0.1:5000/upload
curl -F 'file=@ornek.pdf' http://127.0.0.1:5000/upload
curl http://127.0.0.1:5000/health
```

Dosya isimleri örnektir; kişisel belgeleri test/benchmark için kullanmayın.

## API ve PDF davranışı

Başarılı DOCX/PDF yanıtı `200 {"text":"Çıkarılan metin"}`;
hatalar her zaman `{"error":"Türkçe hata mesajı"}` biçimindedir.
`GET /health` yalnızca API'nin yanıt verdiğini denetler: `{"status":"ok"}`.
Health ve OPTIONS çağrıları belge işçisi başlatmaz.

PDF için `pypdf` seçildi: saf Python, küçük dağıtım paketi, ek sistem aracı veya
yerel derleme gerektirmez; BSD lisanslıdır. PyMuPDF çoğu işte hızlıdır ancak
daha büyük yerel paket ve AGPL/ticari lisans değerlendirmesi gerektirir.
ReportLab yalnızca yapay test PDF'leri üretmek için geliştirme bağımlılığıdır.

- Seçilebilir metin ve kullanılabilir karakter eşlemesi olan PDF'ler desteklenir.
  Türkçe Unicode karakterleri korunur; bozuk/eksik font eşlemeleri onarılamaz.
- Sayfalar sırayla işlenir; aralarına iki satır sonu eklenir. Sayfa sınırlarında
  kelimeler birleştirilmez. Sayfa içindeki görsel satır kaydırmaları ve fazla
  boşluklar sadeleştirilir; mevcut boş satırlar paragraf ayırıcı olarak korunur.
- Okuma sırası PDF'nin metin akışına bağlıdır. Çok sütunlu, tablo ağırlıklı veya
  karmaşık düzenlerde insanın gördüğü sıra birebir elde edilemeyebilir.
- Şifreli PDF'ler (boş şifreli olanlar dahil) kabul edilmez. OCR yoktur;
  yalnızca görüntü içeren taramalar anlaşılır hata alır. Karma belgelerde yalnız
  seçilebilir metin içeren sayfalar döner.
- Bağlantılar takip edilmez; JavaScript, açılış eylemleri ve ekler çalıştırılmaz.
  Kullanıcı girdisiyle URL indirme, komut çalıştırma veya dosya çıkarma yapılmaz.
- DOCX paragraflarının sırası, boş paragraflar ve `\n` sözleşmesi korunur;
  önceki sürüm gibi tabloların/hizaların tamamını aktarma iddiası yoktur.

| Durum | HTTP |
| --- | --- |
| Eksik dosya/ad, boş dosya, uzantı/tür uyuşmazlığı, bozuk belge | 400 |
| Dosya, multipart, sayfa, metin, ZIP veya bellek/CPU sınırı | 413 |
| Şifreli/metinsiz PDF, metinsiz DOCX, süre sınırı veya aniden sonlanan işçi | 422 |
| Beklenmeyen uygulama hatası | 500 |

## Kaynak ve yükleme güvenliği

| Sınır | Varsayılan |
| --- | --- |
| Gerçek dosya baytı | **10 MiB** (10 × 1024 × 1024), dahil |
| Toplam multipart istek | 11 MiB; dosyadan ayrı başlık/alan bütçesi |
| Form alanı bellek bütçesi / parça sayısı | 256 KiB / 8 |
| Tek istekte dosya | 1, `file` alanında |
| PDF sayfası | 100 |
| Çıkarılan toplam metin | 1.000.000 karakter, sayfa/paragraf ayırıcıları dahil |
| Bir PDF sayfasının açılmış içerik akışı | 8 MiB |
| DOCX toplam açılmış ZIP içeriği / üye sayısı | 32 MiB / 1.000 |
| DOCX üye başına sıkıştırma oranı | 200 |
| Ayrıştırıcı süreç adres alanı / CPU | 256 MiB / 5 saniye |
| İşçi yanıtını bekleme | 8 saniye |

Uzantıya ve MIME'a tek başına güvenilmez: imza, gerçek PDF/OOXML yapısı ve
ayrıştırıcı sonucu denetlenir. DOCX'te zorunlu paket üyeleri, açılan gerçek
baytlar ve CRC kontrol edilir; yinelenen/tehlikeli yollar, şifreli veya
desteklenmeyen sıkıştırmalı üyeler reddedilir. Çok tekrarlı ama geçerli belgeler
de kaynak sınırlarına takılabilir; daha küçük belge yüklenmelidir.

Her ayrıştırma ayrı, tek kullanımlık bir süreçte yapılır. Ana API canlı kalır;
zaman aşımında süreç öldürülür ve temizlenir. PDF akışı kontrol edilmeden önce
açılabilir ve PDF'nin font/XObject yapıları da kaynak tüketebilir; bu yüzden
sayfa/metin limitlerine ek olarak işletim sistemi limitleri uygulanır.
Forkserver ve parser modülleri bir kez yüklenir; her istekte temiz bir işçi
oluşturulur. İşçi sonucu süreç beklenmeden okunur; büyük yanıtlarda pipe
kilitlenmesi önlenir. Sonuç aktarımı da metin üst sınırıyla sınırlıdır.

Belge kalıcı olarak kaydedilmez. Multipart işleyici büyük dosyaları geçici
dosyaya taşır; istek kapanınca temizler. Belge ana süreçte bir kez, sınırlı
okunur ve IPC ile işçiye aktarılır; 10 MiB'ye kadar ek bellek kopyaları gerekir.
İşçi limiti tüm servisin toplam RAM limiti değildir. Forkserver, ana uygulama
ve eşzamanlı yüklemeler ek RAM kullanır. Yanıtlar `Cache-Control: no-store` ve
`X-Content-Type-Options: nosniff` alır. İç hata ayrıntıları, belge metni ve dosya
adı uygulama loglarına yazılmaz. Bu API kimlik doğrulaması sağlamaz; CORS
tarayıcı erişim kuralıdır, istek hızını veya tarayıcı dışı erişimi sınırlamaz.

## CORS

Yalnızca `/upload` için şu origin'ler açıktır:

- `https://okumadunyasi.vercel.app`
- `http://localhost:3000`, `http://localhost:5173`
- `http://127.0.0.1:3000`, `http://127.0.0.1:5173`

Ek yerel/önizleme adresleri `CORS_ORIGINS` ortam değişkeninde virgülle ayrılmış
tam origin olarak tanımlanabilir. Örnek:
`CORS_ORIGINS=https://okumadunyasi-preview-123.vercel.app,http://localhost:8080`.
Adresler şema ve portuyla tam eşleşir; wildcard/regex önizleme izni yoktur.
Hatalı yapılandırma başlangıçta reddedilir. CORS credentials kapalıdır.

## Süre ölçümü ve performans

Her upload uygulama logunda yalnızca HTTP durumu, izinli format etiketi ve
sayısal süreleri üretir. Aynı ölçümler `Server-Timing` başlığında bulunur:

- `receive`: Flask handler girişinden multipart çözümleme/dosyanın alınmasına kadar.
- `validation`: boyut/imza kontrolü ve işçi içindeki yapısal doğrulama.
- `extraction`: işçi içinde metin çıkarma ve PDF temizliği.
- `worker`: süreç başlatma, doğrulama/çıkarma, IPC ve süreç temizliği toplamı.
- `json`: JSON yanıtının hazırlanması; `total`: Flask upload işleme toplamı.

`worker`, `validation` ve `extraction` sürelerini kapsar; tüm sütunlar
toplanmamalıdır. Başarısız aşamaların tamamlanmamış ölçümleri sıfır kalabilir.
Bu ölçümler istemcinin yükleme başlangıcını, proxy/Gunicorn kuyruğunu, yanıt
indirmesini veya Render'ın Flask'a ulaşmadan önceki uyanma süresini kapsamaz.
Multipart alma ölçümü, WSGI'nin önceden tamponlamadığı aktarım beklemesini
kapsayabilir; istemci ağı ile sunucu çözümleme maliyetini tek başına ayıramaz.

Yerel ölçüm (15 Ekim 2026): Linux, Python 3.12.14, seri Flask test istemcisi,
aynı bağımlılık ortamı, 5 ısınma + 30 ölçüm. Baseline
`bfdb95ba721c3aef584fee995f33a5bd9d0a1faa` içindeki eski handler algoritmasıdır.
Ölçümler ağsızdır; Render sonuçları veya kapasite garantisi değildir.

| Yapay belge | Boyut | Eski medyan / p95 | V2 medyan / p95 |
| --- | --- | --- | --- |
| DOCX, 2 paragraf | 36.645 B | 6,17 / 13,45 ms | 29,46 / 34,57 ms |
| DOCX, 200 paragraf | 37.267 B | 11,84 / 27,19 ms | 33,22 / 36,50 ms |
| PDF, 1 sayfa | 22.215 B | Desteklenmiyordu | 21,33 / 27,12 ms |
| PDF, 10 sayfa | 26.608 B | Desteklenmiyordu | 30,78 / 37,25 ms |

İlk V2 DOCX isteği, forkserver/işçi hazırlığı dahil yaklaşık 153 ms sürdü.
Küçük DOCX'te aşama medyanları: alma 0,78 ms; doğrulama 1,75 ms; metin çıkarma
7,81 ms; işçi toplamı 27,59 ms; JSON 0,06 ms. Küçük belgelerde maliyetin
çoğu doğrulama ve süreç yalıtımıdır; JSON bir darboğaz değildir.
DOCX bu sürümde hızlanmadı: güvenlik ek maliyeti açıkça görülmektedir.
İşçi kitaplıklarının önceden yüklenmesi tekrar eden import maliyetini azaltmayı
amaçlar; zaman/bellek sınırları büyük veya bozuk belgelerde sınırsız beklemeyi önler.

Gunicorn 26.2.0, tek sync worker üzerinden yerel HTTP ile ayrıca Unicode
DOCX/PDF, çok sayfalı PDF, yaklaşık 98.000 karakterlik yanıt ve hatalı/büyük
dosyalar doğrulandı. Bu test gerçek soket/WSGI yolunu kapsar; interneti kapsamaz.
30 sıcak küçük DOCX isteğinde medyan 38,79 ms, p95 62,23 ms; yeniden başlatılan
işçinin ilk isteği 160,91 ms ölçüldü. HTTP ölçümü ayrı koşudur; makine yükü,
bağlantı kurulumu ve WSGI maliyeti test istemcisi sonuçlarından farklıdır.

Render ücretsiz planda uyuyan servisin uyanma gecikmesi Python işleme süresinden
ayrıdır. Canlı servise ölçüm amaçlı kişisel belge gönderilmedi; canlı soğuk
başlangıç süresi ölçülmedi. Bu kod o gecikmeyi tamamen ortadan kaldırmaz.

Tekrarlamak için (depo kökünde):

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/benchmark_uploads.py \
  --baseline-ref bfdb95ba721c3aef584fee995f33a5bd9d0a1faa \
  --samples 30 --output /tmp/okumadunyasi-benchmark.json
```

## Test ve kod kalitesi

```bash
.venv/bin/python -m pytest -q tests
.venv/bin/ruff check app.py document_parser.py scripts tests
.venv/bin/ruff format --check app.py document_parser.py scripts tests
.venv/bin/python -m pip check
```

86 test; yapay DOCX/PDF üretimi, Türkçe karakterler, önceki DOCX sözleşmesi,
sayfa sınırları, bozuk/şifreli/taranmış belgeler, tam 10 MiB ve multipart ek yükü,
ZIP/CRC/XXE kontrolleri, kaynak limitleri, CORS, gizli hata dönüşleri, log
gizliliği, zaman aşımı ve işçi temizliğini kapsar. Süre metriklerinin varlığı da
test edilir; makine hızına bağlı, rastlantısal bir milisaniye başarı eşiği yoktur.

## Render için öneriler (uygulanmadı)

Depoda Render manifesti veya mevcut servis ayarlarını belgeleyen dosya yoktur.
Render'a deployment yapılmadı; ayarları değiştirmeden önce şunları değerlendirin:

1. `gunicorn app:app` uyumlu kalır. 512 MiB planlarda başlangıç için tek sync
   worker/tek thread önerilir; worker sayısıyla tüm parser ve yardımcı süreçler
   çoğalır. Gerçek RAM/eşzamanlılık ölçülmeden worker sayısını artırmayın.
2. Sağlık kontrolü için `/health` kullanılabilir. Bu adresi ücretsiz planın
   uyumasını zorlamak için sürekli çağırma düzeni eklenmedi.
3. Sürekli gecikmenin önce istemci zamanları ve `Server-Timing` ile ağ, servis
   uyanması ve belge işlemesine ayrılması gerekir. Sürekli sıcak servis ihtiyacı
   varsa plan seçimi ayrıca değerlendirilmelidir.
4. Daha yüksek trafik için kimlik doğrulama, edge hız sınırlaması ve sınırlı iş
   kuyruğu ayrı çalışma olarak ele alınabilir; bu PR ek servis gerektirmez.

## Sonraki frontend işi (bu depoda uygulanmadı)

- Dosya seçicide `.docx,.pdf` ve uygun MIME türleri; iki format için sürükle-bırak.
- Dosya başına mevcut 10 MiB kontrolünü koruma, iki uzantıyı doğrulama; gerçek
  tür doğrulaması için backend sonucunu esas alma.
- Yükleme sürerken ve yanıt beklenirken anlaşılır, ayrı durum mesajları;
  `400/413/422/500` JSON `error` alanlarını kullanıcıya gösterme.
- Başarıda mevcut `text` alanını aynen kullanma; ayrı PDF yanıt akışı gerekmiyor.
- Gerçek ağ yükleme ilerlemesi kullanılabilir. Backend işleme yüzdesi bu senkron
  endpoint'ten ölçülemez; uydurma yüzde gösterilmemeli. Taranmış PDF/OCR
  sınırlaması ve ücretsiz Render uyanma beklemesi uygun mesajla açıklanmalı.
- Önizleme adreslerini gerektiğinde backend `CORS_ORIGINS` listesine tek tek ekleme.
