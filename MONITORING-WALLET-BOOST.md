# Direct wallet-boost copy model — izleme planı

Copy trade sinyalleri artık **Bayesian değil, wallet score tabanlı heuristik** ile üretiliyor:  
"Bu wallet iyiyse, piyasanın biraz önünde olduğunu varsayıyorum."  
Başarı kriteri: **PF ve işlem başı PnL**, accept oranı değil.

---

## Stale_data exit fix — ne değişti?

**Eski bug:** Stale kapanışta çıkış fiyatı = `entry` → her trade **garanti küçük zarar** (fee + slippage). Execution katmanı edge’i siliyordu; bu yüzden PF/avg_pnl yapay düşüktü.

**Fix:** Stale kapanışta çıkış fiyatı = `snap.midpoint or snap.last_trade_price or entry` → piyasa ne yaptıysa o yansıyor. Artık ölçüm **gerçek**.

- PF / avg_pnl iyileşmesi = “model iyi oldu” değil, **“ölçüm doğru oldu”**.
- 12 saat sonra bak: **exit_reason dağılımı** + **PnL exit_reason’a göre**. Stale_data share hâlâ çok yüksekse sorun **market selection** (hangi market’lere giriyoruz), exit değil.
- Yorum: **Sistem gerçekten edge üretiyor mu, yoksa sadece daha az zarar mı yazıyor?** — exit_reason + PnL dağılımı bunu söyler.

---

## İlk 6 saat — Teknik sağlık + canlılık

- [ ] Crash / restart yok
- [ ] **`signals_passed_filter > 0`** (veto: sistem canlı ama komadaysa iş görmez)
- [ ] **`trades_executed > 0`** (veto: aynı sebep)
- [ ] Exit engine çalışıyor
- [ ] `stale_data_share` patlamıyor

Funnel’dan özellikle `signals_passed_filter` ve `trades_executed` bakılsın.

**Komutlar:**

```powershell
docker logs kripto-bot-backend-1 --since=6h 2>&1 | Select-String "Runner cycle|Exit engine|crashed|ERROR"
$f = Invoke-RestMethod "http://localhost:8002/api/analytics/strategy-conversion-funnel?lookback_hours=6"
# Veto: aşağıdakiler > 0 olmalı
$f.signals_passed_filter
$f.trades_executed
Invoke-RestMethod "http://localhost:8002/api/trades?limit=20"
```

---

## 12 saat — Ekonomik yön

- [ ] `direct_copy_pf`
- [ ] `high_conviction_pf`
- [ ] `avg_pnl_per_trade`
- [ ] `trading_only_pf`
- [ ] `top_reject_reason`
- [ ] `positive_executable_share`
- [ ] **exit_reason dağılımı** + **PnL exit_reason’a göre** (stale_data fix sonrası gerçek edge mi, yoksa sadece daha az zarar mı — buna göre yorumlanır)
- [ ] **Açık pozisyon:** sayı + son 12 saatte açılan trade’e oranı + yaş dağılımında **>4h pozisyon payı**  
  (15 açık bazen normaldir, bazen sistem el freninde; oran ve >4h payı anlam verir.)

**Komut (tek bakış):**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "phase-decision-wrapper.ps1" -BaseUrl "http://localhost:8002" -CutoffHours 12
```

Açık pozisyon sayısı / oran / >4h payı için ayrıca analytics veya DB sorgusu gerekebilir.

**exit_reason + PnL dağılımı:** Kapanan işlemleri `exit_reason` ve `realized_pnl` ile çekmek için `/api/trades?limit=200` (veya ilgili analytics endpoint’leri) kullanılabilir. Birkaç saat sonra bu dağılım atılırsa yorum netleşir: sistem gerçekten edge üretiyor mu, yoksa sadece daha az zarar mı yazıyor.

---

## 24 saat — Hüküm (eşiklerle)

**Soru:** Edge gerçekten var mı, yoksa wallet boost sadece trade açtırıp hâlâ kaybettiriyor mu?

### Başarıya yakın

- `direct_copy_pf >= 1.0` veya baseline üstü
- `avg_pnl_per_trade` baseline üstü / daha az negatif
- `stale_data_share` kötüleşmiyor
- `positive_executable_share` düşmüyor

### Başarısız

- PF belirgin kötü
- avg pnl/trade kötü
- stale yüksek ve artıyor
- Reject reason dağılımı “edge yok” diye bağırıyor (ağırlık CONF_EDGE_LOW, vb.)

24 saatte “iyi hissediyoruz” değil, **eşikler geçti / geçmedi** ile karar verilir.

---

## Sonraki mantıklı A/B test: `COPY_EDGE_BOOST`

| | Kontrol | Test |
|---|--------|------|
| **COPY_EDGE_BOOST** | 0.30 | 0.20 |

**Hipotez:** PF korunur, avg PnL/trade bozulmazsa, daha az overfit ile daha stabil sistem.

**Ölçülecekler:** direct_copy_pf, avg_pnl_per_trade, n_wallets_contributed, top_5_wallet_share, **positive_executable_share**.  
(Boost düşünce PF aynı kalabilir ama sistem çok fazla candidate’ı kesip kuruyabilir; positive_executable_share bunu yakalar.)

---

## İsimlendirme

- **Bayesian copy model** denmemeli.
- **Direct wallet-boost copy model** kullanılmalı.
