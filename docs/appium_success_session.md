Appium'da başarılı bir oturum (session) kurulduktan sonra, mevcut ekranın DOM (sayfa veya uygulama kaynağı) yapısına doğrudan API üzerinden erişmek için GET /session/:sessionId/source HTTP isteğini (request) kullanmalısınız
.
Bu istek W3C WebDriver standart protokolünün bir parçasıdır ve şu detaylarla çalışır:
HTTP Metodu: GET
Uç Nokta (Endpoint): /session/:sessionId/source (Buradaki :sessionId, oturumu ilk başlattığınızda Appium sunucusunun size döndürdüğü benzersiz oturum kimliğidir).
Dönen Yanıt (Response): O anki aktif ekranın (veya tarama bağlamının) tüm DOM yapısını metin (string) formatında döndürür
.
Eğer yerel (native) bir mobil uygulama ekranındaysanız bu yanıt XML formatında gelir.
Eğer bir mobil tarayıcı (Chrome/Safari) veya WebView (hibrit uygulama) ekranındaysanız yanıt HTML formatında gelir
.

Oturum Kimliği (Session ID) Appium sunucusuna cihaz yeteneklerini iletip yeni bir bağlantı başlattığınızda (POST /session API isteği ile), sunucu size bu bağlantıyı yönetmeniz için benzersiz bir oturum kimliği döndürür
.
Nasıl bulunur: İstemci tarafında oturum açma isteği başarılı olduğunda Appium'un döndürdüğü JSON yanıtının (Response) içinde sessionId anahtarıyla yer alır
. Test senaryolarınızda DOM'a erişmek veya eylemler gerçekleştirmek için kullanacağınız tüm W3C WebDriver API isteklerinde (örneğin GET /session/:sessionId/source) bu ID'yi URL'e eklemeniz gerekir
.
Element Kimliği (W3C Element ID) Ekranda tıklama, metin girme gibi fiziksel etkileşimlerde bulunacağınız her bir DOM öğesinin (UI elementinin) oturum süresince geçerli benzersiz bir kimliği vardır
.
Nasıl bulunur: İlgili elementi bulmak için sunucuya bir konumlandırıcı stratejisiyle (örneğin XPath) arama isteği gönderdiğinizde (POST /session/:sessionId/element API uç noktası veya kod tarafında findElement), Appium size yanıt olarak bir W3C element tanımlayıcısı (örneğin element-6066-11e4-a52e-4f735466cecf formatında bir string) döndürür
. Ardından elementi tıklamak için POST /session/:sessionId/element/:elementId/click isteğini bu kimlik ile yönlendirirsiniz
.
Eğer aradığınız benzersiz kimlik bunlardan farklı bir değer ise (örneğin DOM ağacında geliştiriciler tarafından verilen accessibility id veya resource-id), ilgili elementi incelemek için getPageSource ile sayfa kaynağını çekebilir veya doğrudan Appium Inspector görsel arayüzünü kullanarak elementin niteliklerini (attributes) bulabilirsiniz.