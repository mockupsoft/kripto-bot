# GitHub'a Yükleme

Proje ilk kez [github.com/mockupsoft/kripto-bot](https://github.com/mockupsoft/kripto-bot) reposuna yüklenecek. Aşağıdaki komutları **proje kök dizininde** (`kripto-bot`) çalıştırın.

## 1. Git başlat (henüz yoksa)

```bash
git init
```

## 2. Remote ekle

```bash
git remote add origin https://github.com/mockupsoft/kripto-bot.git
```

SSH kullanıyorsanız:

```bash
git remote add origin git@github.com:mockupsoft/kripto-bot.git
```

## 3. Tüm dosyaları ekle ve commit

```bash
git add .
git status
git commit -m "Initial commit: Polymarket paper trading research platform"
```

`.gitignore` sayesinde `.env`, `node_modules`, `__pycache__`, `pgdata` vb. dahil edilmez. `.cursor/rules` altındaki kural dosyaları **dahil edilir** (başka AI/geliştirici için).

## 4. GitHub'a push

Reponun boş olduğu varsayılıyor; doğrudan `main` branch’e push:

```bash
git branch -M main
git push -u origin main
```

GitHub kimlik doğrulama isteyebilir (PAT, SSH key veya tarayıcı). İlk push sonrası repo güncel olacaktır.

## 5. Sonraki güncellemeler

```bash
git add .
git commit -m "Açıklama"
git push
```

---

**Not:** `.env` asla commit edilmemeli; sadece `.env.example` repo’da bulunur.
