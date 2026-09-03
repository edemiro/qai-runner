Appium Web Tabanlı Inspector Geliştirme Kılavuzu

Bu kılavuz, modern mobil otomasyon ekosisteminde standart haline gelen Appium mimarisini temel alarak, yüksek performanslı ve sürdürülebilir bir web tabanlı Inspector aracı inşa etmek isteyen mimarlar ve teknik liderler için hazırlanmıştır.

1. Giriş ve Mimari Genel Bakış

Appium, klasik bir Client-Server senfonisi üzerine kuruludur. Bu mimaride sunucu (Server) bir "orkestra şefi" rolünü üstlenirken, istemci (Client) ise otomasyon senaryolarını icra eden bir "orkestra" gibidir.

Mimarinin Temelleri

Node.js tabanlı olan Appium sunucusu, bünyesinde platforma özgü komutları icra eden WebDriver implementasyonunu barındırır. İstemci tarafında yazılan yüksek seviyeli test kodları (Java, Python, JS vb.), sunucu tarafından platforma özgü (UiAutomator2, XCUITest) atomik komutlara dönüştürülür. Mimari, Statelessness (durumsuzluk) prensibiyle çalışır; yani her HTTP isteği kendi içinde bağımsız ve atomik bir işlem olarak değerlendirilir.

Protokol Standartları ve Evrim

Appium 2.0+ ile birlikte JSON Wire Protocol (JSONWP) tamamen emekliye ayrılmış ve yerini W3C WebDriver protokolüne bırakmıştır. Bu geçiş, özellikle oturum yönetimi ve yetkilendirme standartlarında kritik değişiklikler getirmiştir.

Protokol Özelliği	JSON Wire Protocol (Legacy)	W3C WebDriver Protokolü (Standart)
Spesifikasyon	Selenium Projesi Kökenli	W3C Küresel Standart
Kabiliyet Formatı	Standart Anahtar-Değer Çiftleri	Vendor Prefix (appium:) Zorunluluğu
Oturum Başlatma	POST /session (3 Parametreli)	POST /session (Modifiye 1 Parametreli)
Hata Yönetimi	Çeşitli Durum Kodları	Standartlaştırılmış HTTP Durum Kodları
Durum	Appium 2.0+ ile kaldırıldı	Appium 2.0/3.0+ Temel Protokolü

2. Ortam Kurulumu ve Bağımlılıklar

Kurumsal bir Inspector aracı geliştirmek için ortam kurulumunun modüler ve tekrarlanabilir olması şarttır.

Sunucu ve Sürücü Provizyonu

1. Hızlı Kurulum: Standart npm kurulumu yerine, tüm bağımlılıkları tek seferde konfigüre eden npx appium-installer komutu tercih edilmelidir.
2. Sürücü Mimarisi: Appium 2.0+ sürümlerinde sürücüler artık sunucu paketinin içinde değil, bağımsız olarak $APPIUM_HOME (varsayılan: ~/.appium) dizininde barındırılır.
3. Yönetim Komutları:
  * Android: appium driver install uiautomator2
  * iOS: appium driver install xcuitest
  * Doğrulama: appium driver list --installed

Ortam Değişkenleri ve Sağlık Kontrolü

Inspector'ın SDK araçlarına erişebilmesi için ANDROID_HOME ve JAVA_HOME değişkenlerinin tanımlanmış olması kritiktir. Kurulumun bütünlüğü appium-doctor (global kurulum: npm install -g @appium/doctor) üzerinden valide edilmelidir.

3. Cihaz Keşfi (Device Discovery) Mekanizması

Inspector'ın cihazları doğru tespit etmesi, ADB katmanındaki kararlılığa bağlıdır.

* Android Tespiti: adb devices çıktısında cihaz "unauthorized" olarak görünüyorsa, cihaz üzerinden USB hata ayıklama yetkisi "Revoke USB Debugging" seçeneğiyle iptal edilmeli ve ADB sunucusu şu sıra ile yenilenmelidir:
  1. adb kill-server
  2. adb start-server
* Bağlantı Kontrolleri: Gerçek cihazlarda ekran kilidinin ve güç tasarrufu modlarının kapalı olması, Inspector'ın soket bağlantısının (Socket hang up) kopmasını engeller.

4. Oturum Oluşturma (Session Creation) ve Kabiliyet Yönetimi

Oturum başlatma, sunucuya gönderilen /session POST isteğiyle gerçekleşir. Inspector geliştirirken, kabiliyetlerin (Capabilities) W3C standartlarına uygunluğu en büyük önceliktir.

Kabiliyet Mühendisliği Tablosu

Kabiliyet	Tip	Zorunlu mu?	Açıklama
platformName	String	Evet	Hedef işletim sistemi (Android, iOS)
appium:automationName	String	Evet	Sürücü seçimi (UiAutomator2, XCUITest)
appium:deviceName	String	Hayır	Cihaz/Emülatör etiketi
appium:udid	String	Hayır	Gerçek cihazlar için benzersiz kimlik
appium:noReset	Boolean	Hayır	Oturumlar arası uygulama verisini korur
appium:appPackage	String	Hayır	Android paket adı

Mimari İpucu: Kod temizliğini sağlamak için tüm appium: ön ekli kabiliyetler tek bir appium:options objesi içinde sarmalanarak gönderilebilir.

5. Ekran Yansıtma (Screen Mirroring) Uygulaması

Web tabanlı bir Inspector, cihazın o anki görsel durumunu Take Screenshot API'si aracılığıyla alır.

* Görüntü Alma: Sunucu, cihaz ekranını Base64 formatında bir veri dizisi olarak döndürür.
* Görsel Konumlandırma: Alınan bu görüntü, web arayüzünde alt katman (base layer) olarak konumlandırılır. Inspector'ın temel işlevi, bir sonraki aşamada elde edilecek XML hiyerarşisini bu görüntünün tam üzerine (overlay) milimetrik olarak oturtmaktır.

6. DOM Kaynağı Erişimi ve XML İşleme

Kullanıcının ekran üzerinde bir elementi seçebilmesi için XML hiyerarşisinin doğru ayrıştırılması gerekir.

XML Ayrıştırma ve Koordinat Matematiği

getPageSource komutu, uygulamanın UI ağacını XML formatında sunar. Her düğüm (node) içinde bulunan bounds verisi elementin sınırlarını belirler.

* Format: [x1,y1][x2,y2] (Burada x1,y1 sol üst, x2,y2 sağ alt koordinattır).
* Merkez Nokta Hesabı: Tıklama (click) eylemlerini simüle etmek için elementin merkez koordinatları şu formülle hesaplanır:
  * x = (x1 + x2) / 2
  * y = (y1 + y2) / 2

Element Süzme ve WebViev Notu

Elementin tipi (class) ve içeriği (text) bu XML ağacından filtre edilir. Eğer hibrit bir uygulama veya WebView üzerinde çalışılıyorsa, elementlerin DOM'da görünebilmesi için Android uygulama kaynak kodunda setWebContentsDebuggingEnabled(true) flag'inin set edildiğinden emin olunmalıdır.

7. Güvenlik Yapılandırması ve Gelişmiş Özellikler

Inspector araçları genellikle sunucu loglarına erişim veya kabuk komutları çalıştırma gibi "güvensiz" özelliklere ihtiyaç duyar.

Appium 3.0 Gelecek Projeksiyonu ve Güvenlik

Appium 2.13'ten itibaren başlayan ve Appium 3.0 ile zorunlu hale gelecek olan güvenlik protokolüne göre, güvenlik bayrakları artık mutlaka bir kapsam ön eki (scope prefix) ile tanımlanmalıdır.

* Hatalı kullanım: --allow-insecure=adb_shell
* Doğru kullanım: --allow-insecure=uiautomator2:adb_shell veya tüm sürücüler için *:adb_shell

Kritik Hata Ayıklama (Troubleshooting)

* iOS "Code 65" Hatası: WebDriverAgent (WDA) başlatılamadığında görülür. Çözüm: Xcode üzerinden manuel imzalama (Manual Signing) yapın, DerivedData klasörünü temizleyin ve cihazda geliştirici sertifikasına güven (Trust) verin.
* WebDriverException / Stability: Android tarafında stabilite sorunları için uiautomator2@2.29.0 veya daha üstü bir sürümün kullanılması mimari olarak önerilir.
* Geriye Dönük Uyumluluk: Appium 1.x mantığıyla çalışan eski scriptlerin Appium 2.0+ sunucusuna bağlanabilmesi için sunucu --base-path /wd/hub parametresi ile başlatılmalıdır.

8. Sonuç ve En İyi Uygulamalar

Başarılı bir mobil otomasyon altyapısı için şu üç prensibi merkezi yapılandırmanıza entegre edin:

1. Protokol Disiplini: Tüm kabiliyet yönetimini W3C standartlarına göre güncelleyin ve vendor prefix kullanımını standartlaştırın.
2. Sürüm Modülerliği: Sunucu ve sürücü güncellemelerini birbirinden bağımsız yöneterek (Independent Release Cadence), yeni işletim sistemi sürümlerine (iOS 17+, Android 14+) hızlı adaptasyon sağlayın.
3. Merkezi Konfigürasyon: Sunucu argümanlarını komut satırı yerine JSON veya YAML formatındaki bir Appium Config File üzerinden yönetin; bu sayede CI/CD süreçlerinde "Single Source of Truth" (tek doğruluk kaynağı) prensibini koruyun.
