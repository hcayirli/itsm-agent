# AI Destekli RCA Agent

Jira ticketlarını otomatik olarak işleyerek ISO 20000/ITIL 4 standartlarına uygun Türkçe Kök Neden Analizi (RCA) raporu üreten ve raporu ilgili Jira ticketına yorum olarak ekleyen bir ITSM otomasyon sistemidir. Tüm süreç web tabanlı bir dashboard üzerinden izlenebilir ve yönetilebilir.

---

## Özellikler

- Jira ticketları webhook veya manuel tetikleme ile otomatik alınır ve RCA analizine gönderilir.
- Üretilen RCA raporu doğrudan Jira'ya yorum olarak yazılır; insan müdahalesi gerekmez.
- Geçmiş RCA raporları ve benzer ticketlar yeni analizde referans olarak kullanılır.
- Dahili bilgi bankası PDF yüklenerek zenginleştirilebilir.
- Tüm konfigürasyon (LLM sağlayıcısı, Jira bağlantısı, eşik değerleri) dashboard üzerinden değiştirilebilir; yeniden başlatma gerekmez.
- Tüm raporlar kalıcı olarak arşivlenir ve geçmişe erişim her zaman mümkündür.

---

## Kullanılan Teknolojiler

| Katman | Teknoloji | Amaç |
|---|---|---|
| Web framework | FastAPI + Uvicorn | API ve webhook yönetimi |
| Ajan orkestrasyon | LangGraph | Çok adımlı AI iş akışını yönetme |
| LLM (bulut) | OpenRouter (gpt-oss-120b) | Rapor üretimi |
| LLM (yerel, opsiyonel) | Ollama (Llama 3.2) | İnternet bağımsız çalışma |
| Bilgi bankası | ChromaDB + sentence-transformers | Semantik arama |
| Kalıcı depolama | MongoDB | RCA arşivi ve ayar yönetimi |
| Jira bağlantısı | MCP (mcp-atlassian) | Ticket okuma ve yorum yazma |
| Konteyner | Docker Compose | Servis orkestrasyonu |
| Arayüz | Tailwind CSS, marked.js | Dashboard |

---

## Gereksinimler

- Docker ve Docker Compose (v2+)
- OpenRouter API anahtarı — veya Ollama kurulu bir sunucu
- Jira Cloud hesabı ve API token
- 16 GB RAM (GPU gerekmez)

---

## Kurulum

**1. Ortam dosyasını oluşturun:**

```bash
cp .env.example .env
```

`.env` içinde şu alanları doldurun:

```bash
OPENROUTER_API_KEY=sk-or-...
JIRA_BASE_URL=https://sirket.atlassian.net
JIRA_USER_EMAIL=user@example.com
JIRA_API_TOKEN=...
```

**2. Servisleri başlatın:**

```bash
docker compose up -d --build
```

**3. Bilgi bankasını yükleyin:**

```bash
docker compose exec app python -m src.rag.ingest
```

Servisler hazır olduktan sonra dashboard `http://localhost:8080` adresinde açılır.

---

## Kullanım

**Dashboard üzerinden:**
1. Sağ üstteki **Ayarlar** butonuna tıklayın; açılan panelde üç sekme bulunur:
   - **LLM sekmesi:** Sağlayıcı (OpenRouter / Ollama), model adı, API anahtarı ve zaman aşımı süresi.
   - **Jira sekmesi:** Jira Base URL, e-posta ve API token.
   - **RAG sekmesi:** Kaç KB sonucu getirileceği ve benzer ticket benzerlik eşiği.
2. Sol paneldeki **Project Key** alanına Jira proje anahtarınızı girin (örn. `HUS`). Boş bırakırsanız tüm projeler taranır.
3. **Fetch & Process All** butonuna tıklayın; projedeki açık ticketlar sıraya alınır ve RCA analizi arka planda başlar. Daha önce işlenmiş ticketlar otomatik olarak atlanır.
4. Tek bir ticketı işlemek için **Tekil ticket** alanına ticket numarasını yazıp (örn. `HUS-1`) yanındaki ▶ butonuna tıklayın.
5. Sol panelden herhangi bir ticketa tıklandığında sağ panelde hangi analiz adımında olduğu, Investigator bulguları, Reviewer geri bildirimi ve üretilen rapor gerçek zamanlı görüntülenir.
6. Sağ üstteki **Promptlar** butonundan System Prompt ve User Template düzenlenebilir; değişiklikler anında devreye girer, yeniden başlatma gerekmez.

**PDF yükleme:**
Sol panelin altındaki **KB'ye PDF Ekle** bölümünden teknik doküman, prosedür veya olay raporu yüklenebilir. İçerik otomatik olarak bilgi bankasına eklenir ve sonraki analizlerde referans alınır.

---

## Veri Hazırlığı ve İşlem Süreçleri

Sistemin analiz yeteneğini ve operasyonel iş akışını sağlamak amacıyla veri hazırlığı ve entegrasyon süreçleri aşağıdaki adımlarla kurgulanmıştır:

**Bilgi Tabanı ve Sentetik Veri Üretimi**
Ajanın geçmiş tecrübelerden faydalanabilmesi için 10 adet sentetik ve semantik açıdan zengin bilgi tabanı (KB) dokümanı üretilmiştir. Bu dokümanlar, RAG (Retrieval-Augmented Generation) mimarisi için Vektör Veritabanına gömülmeye uygun formata getirilmiştir. Aynı şekilde sistemi uçtan uca test edebilmek amacıyla Jira üzerinde tamamen sentetik senaryolardan oluşan çağrılar (ticket'lar) oluşturulmuştur.

**Dashboard ve Ticket Çekme Mekanizması**
Kullanıcı deneyimini kolaylaştırmak amacıyla sistemin merkezine kullanıcı dostu bir web Dashboard'u yerleştirilmiştir. Normal şartlarda Jira'dan gelen yeni ticket'ları anında yakalamak için Webhook mimarisi kurulabilirdi. Ancak projeyi reposundan klonlayan diğer geliştiricilerin dışarıdan erişim için *Ngrok* vb. tünelleme araçlarıyla uğraşmasını önlemek ve "Tak-Çalıştır" (Plug-and-Play) yapısını korumak adına bu yaklaşımdan bilinçli olarak vazgeçilmiştir. Bunun yerine, Dashboard üzerine yerleştirilen bir buton ile Jira'daki açık ticket'ların MCP (Model Context Protocol) üzerinden tek tıkla çekilip işleme alınması sağlanmıştır.

**Hibrit LLM Altyapısı ve Veri Kalıcılığı**
Yerel (offline) LLM kullanımında yaşanabilecek donanım bazlı yavaşlamalara karşı, projenin hızlıca test edilebilmesi için OpenRouter üzerinden API destekli hibrit bir yapı kurulmuştur. İşlem gören ticket'ların durumları MongoDB'de kayıt altına alınarak mükerrer işlemlerin (aynı ticket'ın tekrar işlenmesi) önüne geçilmiştir. Üretilen Kök Neden Analizi (RCA) raporlarının ise son adımda tekrar MCP aracılığıyla ilgili Jira çağrısına yorum (comment) olarak eklenmesi kurgulanmıştır.

---

## Mimari Yaklaşım ve Teknik Tasarım

### Nasıl Çalışır?

Sistem bir ticket aldığında sırayla üç yapay zeka ajanını devreye sokar:

```
Jira Ticket
    │
    ▼
[Araştırmacı (Investigator)]
Ticket içeriğini bilgi bankasıyla ve geçmiş benzer
ticketlarla karşılaştırır; kök neden hipotezleri,
zaman çizelgesi ve CAPA aksiyonlarını bullet-point
listesi olarak çıkarır.
    │
    ▼
[Yazıcı (Drafter)]
Ham bulguları ITIL 4 uyumlu RCA Markdown şablonuna
dönüştürür: Ishikawa, 5 Neden, CAPA tablosu, retrospektif.
    │
    ▼
[Denetçi (Reviewer)]  ──► Hata varsa Yazıcı'ya geri döner (max 2x)
5 kural kontrol eder: emoji yasağı, uydurma veri,
zaman kipi tutarlılığı, meta ifade, Türkçe dil kuralı.
    │
    ▼
Jira'ya yorum olarak yazılır + MongoDB'ye arşivlenir
```

### Agentic Yapı — LangGraph

Üç ajan bağımsız çalışmak yerine bir **durum makinesi** (StateGraph) olarak orkestre edilir. Bu sayede Reviewer bir hata tespit ettiğinde iş akışı otomatik olarak Drafter'a geri döner; onay alındığında ya da iki iterasyon tükendiğinde ilerlemeye devam eder. Her adım dashboard'da gerçek zamanlı görünür.

### RAG — Bilgi Bankası ile Desteklenmiş Üretim

Sistem, LLM'e yalnızca ticket içeriğini değil; ilgili bilgi bankası maddelerini ve geçmişte işlenmiş benzer ticketları da bağlam olarak verir. Bu yaklaşıma **RAG (Retrieval-Augmented Generation)** denir.

- **Bilgi bankası:** 10 kategoride (uygulama, veritabanı, ağ, güvenlik vb.) Türkçe teknik bilgi barındırır. ChromaDB vektör veritabanında saklanır; ticket içeriğine anlam bazlı (semantik) eşleştirme yapılır.
- **Benzer ticket araması:** Daha önce işlenen her ticket vektör olarak kaydedilir. Yeni bir ticket geldiğinde en benzer geçmiş vakalar bulunur ve analize dahil edilir. Bu, LLM'in geçmiş çözümleri "hatırlamasını" sağlar.

Sonuç olarak LLM; genel bilgisiyle değil, kuruma özgü bilgi birikimi ve geçmiş deneyimlerle analiz üretir.

### MCP — Jira ile İzole Bağlantı

Jira entegrasyonu, `mcp-atlassian` adlı bağımsız bir servis üzerinden **MCP (Model Context Protocol)** ile sağlanır. Bu servis ayrı bir Docker container olarak çalışır; Jira kimlik bilgilerini sadece kendisi tutar, ana uygulama bu bilgilere doğrudan erişmez. Jira API'sindeki format değişiklikleri (örn. ADF→Markdown dönüşümü) de bu servis tarafından karşılanır.

---

## Varsayımlar ve Teknik Kararlar

**Kurumsal hafıza:** Sistem, her tamamlanan RCA'yı MongoDB'ye kalıcı olarak arşivler. Silme işlemleri yalnızca ekranı temizler; arşive dokunmaz. Benzer bir ticket geldiğinde eski rapor arşivden çekilir. Bu tasarım, kurumsal hafızanın birikimini korumak için bilinçli seçilmiştir.

**LLM esnekliği:** Bulut tabanlı OpenRouter ve yerel Ollama arasında geçiş dashboard'dan yapılabilir. İnternet bağlantısı veya veri gizliliği kısıtı olan ortamlar için Ollama seçeneği hazır tutulmuştur. OpenRouter'ın ücretsiz katmanındaki rate-limit kısıtları için otomatik bekleme ve yeniden deneme mekanizması yerleşiktir.

**Bilgi bankası eşiği:** Benzer ticket aramasındaki benzerlik eşiği (`%80` varsayılan) dashboard'dan ayarlanabilir. Yükseltilmesi daha az ama daha kesin eşleşme sağlar; düşürülmesi daha geniş ama daha az ilgili sonuçlar döndürür.

**Raporun güvenilirliği:** Reviewer ajanı, LLM'in kaynak metinde bulunmayan bilgi uydurmasını, geçmiş zaman kipiyle çözülmüş gibi yazmasını veya emoji kullanmasını tespit edip reddeder. Kuralı iki kez geçemeyen taslak olduğu gibi kabul edilir; bu, analizin hiç üretilememesi yerine mevcut en iyi taslağın kullanılabilmesini sağlar.

**GPU gerektirmez:** Embedding modeli CPU üzerinde çalışacak şekilde yapılandırılmıştır. Günlük 500 ticketın altındaki hacimler için ek donanım yatırımı gerekmemektedir.
