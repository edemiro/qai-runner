# Senaryo Standartları — Dijital Kanallar

QAi'ın ürettiği her test senaryosu bu iki standarda uyar. Bu dosya, üretici
promptunun ([`scenario_writer.py`](../backend/scenario_writer.py)) tek kaynağıdır:
kural değişirse burası düzeltilir, prompt onu okur.

Kapsam kararı: QAi bir arayüz sürücüsüdür, bir API istemcisi değil. Bu yüzden
üretici yalnızca **E2E** ve **Component** katmanlarında senaryo yazar. Priority
standardının API sütunu burada kasıtlı olarak uygulanmaz — koşulamayacak bir
senaryo üretmek, Test Set'i çalışmayan kayıtlarla doldurmaktan başka işe yaramaz.

---

## 1. Senaryo Yazım Formatı

Her senaryo tek satırdır ve şu kalıba uyar:

```
Modül - Submodül | Data&Ön Koşul - Aksiyon&Beklenen Sonuç
```

* Senaryo **tamamen İngilizce** yazılır. (Açıklama alanı Türkçe olabilir; başlık
  her zaman İngilizce.)
* `|` tanım kısmını (Modül - Submodül) içerik kısmından ayırır.
* İçerik kısmında `-` , Data & Ön Koşul bloğunu Aksiyon & Beklenen Sonuç
  bloğundan ayırır.
* Önce ana modül, sonra alt modül: `Booking - Availability`, tersi değil.

### Bileşenler

| Bileşen | Ne taşır | Örnek |
|---|---|---|
| Data | Rota + uçuş tipi + yolcu sayıları + datasal gereklilikler | `INT [USA] OW 2 ADT 1 CHD 1 INF` |
| Ön koşul | Data dışında gereken durumlar: uçuşa kalan saat, IRROPS, uçuş statüsü, cihaz durumu, ödeme yöntemi, para birimi | `[1. Segment WK]`, `[Slow Internet Connection]` |
| Aksiyon | Test edilen fonksiyonda yapılan işlem | `Buy XBAG+SAF+SEAT Pay with Alipay [EURO]` |
| Beklenen sonuç | Aksiyon sonrası beklenen durum / kontrol | `control Mail PDF`, `control Emd not Assoc.` |

### Köşeli parantez

Bir dataya ait alt detay, o datanın hemen ardından `[ ]` içinde yazılır:
`INT [USA]`, `PETC [Dog]`, `SPEQ [Golf]`, `1 ADT [EXIT SEAT+XBAG]`.
Özel ödeme tiplerinde para birimi **zorunludur**: `Klarna [USD]`, `Alipay [EURO]`.

### Standart kodlar

| Kategori | Kodlar |
|---|---|
| Rota | `OW` `RT` `MC` |
| Uçuş tipi | `DOM` `INT` |
| Yolcu tipleri | `ADT` `CHD` `INF` `STU` `DIS` `VET` `YNG` `YTH` — sayı kodun önüne: `2 ADT 1 CHD` |
| Brand / kabin | `EcoFly` `ExtraFly` `PrimeFly` `ECO` `BUS` |
| Ek hizmetler | `XBAG` `PETC` `AVIH` `OBAG` `LNG` `SAF` `SPEQ` `FQTV` `FQTU` `Mile` |
| Ödeme | `Tip [Para Birimi]` |

Modüller: Booking, Manage Booking, Check-in, Standalone, IRROPS, Miles&Smiles,
Booker/TKPay.

### Kontrol listesi

Bir senaryo bitmeden şunların hepsine "evet" denebilmelidir:

1. Tamamen İngilizce mi?
2. `Modül - Submodül | Data&Ön Koşul - Aksiyon&Beklenen Sonuç` kalıbına uyuyor mu?
3. Rota ve uçuş tipi standart kodlarla mı?
4. Yolcu tipleri ve sayıları standart kodlarla mı?
5. Alt detaylar `[ ]` içinde mi?
6. Özel ödeme tipinde para birimi var mı?
7. Beklenen sonuç / kontrol noktası açıkça yazılmış mı?

---

## 2. Priority Belirleme

Priority, senaryonun **teknik etkisini** ölçer: *"Bu senaryo fail olursa ne olur?"*
Yani priority, o senaryonun koruduğu işlevin bozulması durumunda oluşacak etkidir.

### Karar akışı — dört soru

1. **Etki alanı:** Fail olursa ana akış mı durur, yan akış mı, tek bir alan mı bozulur?
2. **Yayılım:** Tüm yolcular mı, belirli segment/kanal/cihaz mı, dar bir edge-case mi?
3. **Alternatif:** Kullanıcı işini başka bir akışla tamamlayabiliyor mu?
4. **Risk:** Veri bütünlüğü, finansal, güvenlik veya yasal etki var mı?
   **Varsa seviye en az High.**

### Seviye kriterleri

| Seviye | E2E | Component |
|---|---|---|
| **Critical** | Ana yolculuk tamamlanamaz (rezervasyon, ödeme, check-in, bilet üretimi). Finansal veya veri kaybı riski. | Bileşen bozulursa kullanıcı adımı hiç geçemez ve alternatif ekran yoktur. |
| **High** | Ana akış tamamlanıyor ama önemli bir yan adım (koltuk, ek hizmet, ek bagaj) ciddi bozuk. Geniş kitleyi etkiliyor. | Bileşen yanlış davranıyor; adım ancak tekrar deneme veya farklı yolla tamamlanıyor. Kullanıcıyı yanlış işleme yönlendiren gösterim. |
| **Medium** | Akış tamamlanıyor; bilgilendirme veya ikincil ekranda anlamlı sapma var, geçici çözümle sürdürülebilir. | Gösterim veya bilgi hatalı ama işlem tamamlanıyor. Sıralama/filtreleme sapmaları. |
| **Low** | **Önerilmez.** Görsel bulgu E2E değil, Component katmanında senaryolaştırılır. | Görsel, hizalama, etiket, ikon, dil/format farklılıkları. |

### Kalibrasyon kuralları

* Aynı işlev farklı katmanda farklı priority alabilir.
* **Tavan kuralı:** Bir senaryonun priority'si, koruduğu iş akışının priority'sini aşamaz.
* Negatif/validasyon senaryoları pozitif muadilinden **bir bant aşağıdadır.**
  İstisna — aşağı inmeyenler: güvenlik, yetkilendirme, ücret/fiyat, veri yazma.
* **E2E'de Low azdır, Critical çoktur.** E2E zaten ana akışları korumak için yazılır.

### Priority'yi etkilemeyen konular

Bunlar koşum sırasını etkileyebilir, senaryonun teknik etkisini değiştirmez:
yeni geliştirme olması, regresyon paketinde yer alması, otomasyona alınmış olması,
koşum süresi, sprint hedefi veya release tarihi, senaryoyu yazan kişi.

---

## 3. Örnekler

### E2E

| Priority | Senaryo |
|---|---|
| Critical | `Booking - Payment \| DOM OW 2 ADT - Complete flight search, passenger info and payment, control PNR and ticket number` |
| High | `Check-in - Seat \| INT RT 1 ADT [iOS] - Complete check-in but fail seat change, control seat step error` |
| Medium | `IRROPS - Notification \| DOM OW 1 ADT [Flight Time Changed] - Control banner time against MyTrips detail` |

### Component

| Priority | Senaryo |
|---|---|
| Critical | `Booking - Payment \| INT OW 1 ADT [Valid Card] - Enter card details and control Pay button becomes enabled` |
| High | `Check-in - Seat Map \| DOM OW 1 ADT [Occupied Seats] - Control occupied seats are shown disabled` |
| Medium | `Miles&Smiles - Balance Card \| 0 Mile Account - Control balance shows "0" instead of empty` |
| Low | `Standalone - Additional Services \| DOM OW 1 ADT - Control extra baggage card icon alignment` |
