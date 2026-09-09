# Vaka Çalışması: GES Gizli Üretim Kaybı Tespit Motoru

**Müdahale:** Bir GES sahasında panel kirlenmesi ve invertör düşük performansını, fatura kesilmeden **45 dakika** içinde parasal karşılığıyla birlikte operasyon ekibinin önüne koyan otomatik uyarı motoru.

> Bu belgedeki tüm sayılar, depodaki sentetik saha simülatöründen (`seed=42`, 14 gün, 5 invertör / 650 kWp) üretilmiştir ve `scripts/evaluate_detection.py` ile yeniden üretilebilir. Ham çıktı: [`benchmarks/detection_benchmark_seed42_14d.json`](../benchmarks/detection_benchmark_seed42_14d.json). Gerçek bir müşteri sahasının sonucu değildir; motorun ölçülebilirliğini kanıtlayan kontrollü bir referans testidir.

---

## 1. İş Problemi ve Maliyeti

Bir GES operatörü, üretim düşüşünü genellikle **ay sonu faturasında** fark eder. Kirlenme (soiling) kaynaklı %12–15'lik bir kayıp, günlük üretim grafiğinde "bulutlu gün" gibi görünür; kimse müdahale etmez.

Referans senaryoda ölçülen kayıp:

| Gösterge | Değer |
|---|---|
| İzlenen dönem | 14 gün, 5 invertör, 650 kWp |
| Beklenen üretim | 50.089,9 kWh |
| Gerçekleşen üretim | 48.546,4 kWh |
| Gizli kayıp | **1.535,1 kWh (beklenenin %3,08'i)** |
| Dönemsel nakit karşılığı ($0,10/kWh) | **153,51 $** |

Yıllığa ve saha büyüklüğüne taşındığında (aynı kayıp oranıyla, doğrusal ekstrapolasyon):

- 650 kWp saha: **~40.023 kWh/yıl ≈ 4.002 $/yıl**
- Birim: **~6,16 $/kWp/yıl**
- 10 MWp saha karşılığı: **~61.573 $/yıl**

Bu kaybın kritik özelliği görünmez olmasıdır: SCADA'da hata kodu yoktur, invertör "çalışıyor" der. Kayıp yalnızca *gerçekleşen ile beklenen* karşılaştırıldığında ortaya çıkar.

---

## 2. Operasyonel Müdahale

Darboğaz "veri eksikliği" değil, **karar eşiği eksikliğiydi**. Sistem, her 15 dakikalık dilim için beklenen üretimi modelleyip sapmayı bir iş kuralına bağlar.

```
Saha telemetrisi (15 dk)
   └─> POST /ingest  (API anahtarı, Pydantic doğrulama)
         ├─> DuckDB'ye upsert  (inverter_id, ts_utc benzersiz)
         ├─> RandomForest beklenen kW  (nameplate'e kırpılır)
         ├─> Uygunluk kapısı: ışınım >= 50 W/m2 ve beklenen >= max(0,5 kW, %5 kapasite)
         ├─> Ardışık 4 uygun dilim >= %10 sapma  → PostgreSQL'de tek "açık" alarm
         └─> 4 ardışık sağlıklı dilim            → alarm otomatik kapanır
   └─> Streamlit kokpit: kayıp kWh ve $ karşılığı ile iş emri tablosu
```

Üç tasarım kararı sonucu doğrudan etkiledi:

1. **Gece kapısı.** Beklenen üretim sıfıra yaklaştığında oran hesaplanmaz. Bu olmadan sistem her gece her invertör için alarm üretirdi.
2. **Durum diskte tutulur.** "Ardışık 4 dilim" bellekteki sayaçla değil, DuckDB'deki takvime hizalı UTC dilimleriyle hesaplanır; yeniden başlatma veya veri boşluğu seriyi bozar, uydurma alarm üretmez.
3. **Güç değil enerji fiyatlanır.** `kayıp_kWh = max(0, beklenen − gerçekleşen) × 0,25 s`. Bu çarpan olmadan yöneticiye gösterilen zarar **4 kat şişerdi**.

Beklenen üretim modeli, şeffaf fiziksel referansı (`P = Pdc × G/1000 × (1+γ(T−25)) × PR`) geçmek zorundadır; geçemezse eğitim işi hata verir.

---

## 3. Ölçülebilir Çıktı

`scripts/evaluate_detection.py` çıktısı (etiketler yalnızca puanlamada kullanılır, modele veya API'ye girmez):

| Metrik | Sonuç | Anlamı |
|---|---|---|
| Kirlenme yaşanan invertör-günü | 14 | Referans senaryodaki gerçek olay sayısı |
| Yakalanan | **14 / 14 (%100)** | Hiçbir kirlenme günü gözden kaçmadı |
| Açılan alarm sayısı | 11 | Ardışık günler tek olaya birleşir |
| Gerçekten kirli günde açılan alarm | **11 / 11 (%100)** | Temiz günde açılan sahte alarm yok |
| Tespit gecikmesi (medyan / maksimum) | **45 dk / 45 dk** | Kuralın tanımı gereği 4 dilim, sapma yok |
| Gece satırı / gece sapması / gece alarmı | 3.096 / **0** / **0** | Gece yanlış pozitif tamamen elendi |
| Model hatası (RMSE) | **1,01 kW** (fizik referansı 3,18 kW) | Referanstan **%68,4** daha isabetli |
| Alarmların fiyatladığı kayıp | 1.168,3 kWh = **116,83 $** | Gerçek kaybın **%76,1**'i iş emrine dönüştü |
| Metrik sorgusu (14 günlük agregasyon) | **~0,09 sn** | Toplantıda anlık senaryo sorgusu |

**Tespit süresi:** ay sonu fatura incelemesine kıyasla (30 güne kadar) **45 dakika** — yaklaşık **960 kat** daha hızlı müdahale penceresi.

**Bilinen sınır (dürüst raporlama):** Alarm, iyileşme doğrulanana kadar açık kaldığı için alarm penceresi sonraki temiz günlere taşabiliyor; pencere-kapsama bazında kesinlik %58,3'e düşüyor (61 temiz invertör-gününün 10'una temas). Bu bir yanlış tespit değil, "kapanış gecikmesi"dir: alarmların **hiçbiri** temiz bir günde açılmadı. İyileştirme yolu, kapanış için gereken sağlıklı dilim sayısını yapılandırılabilir yapmaktır.

---

## 4. Üretim Güvencesi

Sistemin sessizce yanlış çalışmasını engelleyen korumalar:

- **Test kapsamı:** 63 pytest testi, `ges_intel` üzerinde **%92 dallanma kapsamı** (alt sınır %80, CI'da zorunlu). Testler doğrudan riskli yolları hedefler: beklenen=0, 3'e karşı 4 dilim, boşlukta sıfırlama, tekrarlı ingest, kW→kWh çarpanı, eğitim sızıntısı.
- **Veri bütünlüğü:** `(inverter_id, ts_utc)` benzersiz anahtar + upsert; aynı veri iki kez gönderilse bile kayıp iki kez sayılmaz, alarm çiftlenmez.
- **Alarm yaşam döngüsü:** İnvertör başına tek açık alarm (kısmi unique index), yükselen kenarda açılış, iyileşmede otomatik kapanış. Uzun süren olay 96 satır çöp üretmez.
- **Model yönetişimi:** Sürümlenmiş joblib artefaktı + metadata (RMSE, özellik sırası, seed); her alarm kendisini üreten `model_version` ile kaydedilir. Model yoksa API fiziksel referansa düşer ve bunu loglar.
- **Sızıntı koruması:** Eğitim yalnızca `is_anomaly=false` satırlarında yapılır; anomali etiketi ve `true_expected_kw` API şemasında yoktur.
- **Hata sözleşmesi:** Doğrulama 422, bilinmeyen invertör 400, veritabanı arızası 503 — iç detay sızdırmadan. Tüm uçlarda `X-API-Key`.
- **Sır yönetimi:** Kimlik bilgileri git'e girmez; Compose `env_file` ile okur, `init-env` komutu yerel `.env` üretir. GitGuardian ve Sourcery taramaları temiz.
- **Yeniden üretilebilirlik:** `seed=42` ile simülasyon, eğitim ve değerlendirme birebir tekrar edilebilir.

---

## Tek Cümlelik Sunum

> "Panel kirlenmesi ve invertör düşük performansını ay sonu faturası yerine **45 dakika** içinde, kaybı doğrudan **dolar cinsinden** iş emrine dönüştürerek yakalayan uyarı motoru — referans testinde kirlenme günlerinin **%100'ünü**, sahte alarm üretmeden tespit etti."

## CV / LinkedIn Entry

**Freelance AI & Data Engineer — Solar Asset Performance Intelligence**
Contract project for renewable-energy asset owners and O&M operators | Remote | 2026

- Designed and shipped an end-to-end alerting engine that detects hidden solar production losses (panel soiling, inverter underperformance) **within 45 minutes instead of at month-end invoice review**, converting every shortfall into a priced work order (`lost kWh × tariff`); on a 650 kWp reference fleet it surfaced **~$4k/yr of otherwise invisible loss (~$6/kWp/yr, ≈$60k/yr at 10 MWp scale)**.
- Trained a RandomForest expected-power model on anomaly-free telemetry (irradiance, temperature, hour, capacity) that **beat a transparent physics baseline by 68% (RMSE 1.0 kW vs 3.2 kW)** and enforced that baseline as a training quality gate so a weaker model can never ship.
- Engineered a night/low-irradiance eligibility gate and a disk-backed four-slot streak rule in DuckDB, achieving **100% recall on soiling inverter-days and zero false alarms opened on clean days** across 3,096 night rows in benchmark testing; alerts persisted in PostgreSQL with one-open-alert-per-inverter idempotency and rising-edge/auto-resolve lifecycle.
- Delivered as a production-ready stack (FastAPI, Streamlit, PostgreSQL/DuckDB, Docker Compose, Alembic) with **63 tests and 92% coverage**, API-key auth, explicit 422/400/503 error contracts, versioned model artifacts, and secret-free CI (GitGuardian and Sourcery clean).

### Yeniden üretme

```bash
pip install -e ".[dev]"
python scripts/evaluate_detection.py --n-days 14 --seed 42 --json benchmarks/detection_benchmark_seed42_14d.json
python -m pytest --cov=ges_intel
```
