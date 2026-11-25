# Black Sea Way

Сайт: https://blackseaway.github.io/blackseaway/ (EN / RO / BG)

Незалежний моніторинг обстрілів у Чорному морі, морської логістики, страхування та цін. Сайт сам збирає новини кожні 3 години, перекладає заголовки через DeepL, рахує статистику (теми, гарячі точки, карта) і публікує пости у Facebook, Instagram і Threads.

## Як це працює

| Що | Де |
|---|---|
| Збір новин (Google News + морські медіа) | `scripts/fetch_news.py`, джерела й ключові слова в `config/sources.json` |
| Генерація сайту | `scripts/build.py`, шаблони в `templates/`, стилі в `assets/` |
| Тексти інтерфейсу трьома мовами | `config/i18n.json` |
| Автопостинг FB / IG / Threads | `scripts/social_post.py` |
| Розклад | `.github/workflows/update.yml` (кожні 3 години + після кожної зміни в репозиторії) |

## Одноразове налаштування (робиться вручну в GitHub)

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. **Settings → Actions → General → Workflow permissions: Read and write permissions** → Save.
3. **Settings → Secrets and variables → Actions → New repository secret**:

| Секрет | Звідки взяти | Обов'язковий? |
|---|---|---|
| `DEEPL_API_KEY` | deepl.com → API Keys (ключ Free закінчується на `:fx`) | так, для перекладу |
| `FB_PAGE_ID` | ID Facebook-сторінки | для Facebook |
| `FB_PAGE_TOKEN` | безстроковий токен сторінки (Graph API Explorer) | для Facebook та Instagram |
| `IG_USER_ID` | ID Instagram-бізнес-акаунта, прив'язаного до сторінки | для Instagram |
| `THREADS_USER_ID` | ID профілю Threads | для Threads |
| `THREADS_TOKEN` | довгостроковий токен Threads (діє 60 днів) | для Threads |
| `GH_PAT` | Fine-grained token: доступ лише до цього репозиторію, *Secrets: Read and write* | для автооновлення токена Threads |

Якщо якогось секрету немає, відповідна частина просто пропускається: сайт працює і без соцмереж, а без DeepL показує заголовки мовою оригіналу.

4. **Actions → Update site → Run workflow**, щоб уперше зібрати новини.

## Робота редакції

**Нова стаття:** створити файл `content/posts/РРРР-ММ-ДД-коротка-назва.md`:

```markdown
---
title: "Заголовок англійською"
date: 2026-12-01
summary: "Один-два речення для анонсу і соцмереж"
author: Galiia Koshel
draft: false
---
Текст статті у форматі Markdown. ## Підзаголовок, **жирний**, [посилання](https://...)
```

- Після збереження сайт оновиться за 1–2 хвилини. RO і BG версії перекладаються автоматично через DeepL.
- Щоб дати власний переклад замість машинного, створіть поруч файл з тією ж назвою і суфіксом мови: `2026-12-01-коротка-назва.ro.md` або `.bg.md`.
- `draft: true` означає, що стаття не публікується. `social: false` означає, що стаття не піде в соцмережі.
- Нова стаття (до 14 днів) має пріоритет в автопостингу перед новинами.

**Ключові показники:** `data/indicators.json`. Заповнити `value`, `date`, `source` (назва + посилання), поставити `"published": true`.

**Джерела та ключові слова:** `config/sources.json`. Щоб прибрати нерелевантні новини, додайте слово в `exclude`.

**Ліміти:** не більше 4 автопостів на добу і не частіше ніж раз на 3 години (`config/site.json` → `social_posting`). DeepL: перекладається до 6 заголовків за запуск, а при 85% місячного ліміту переклад автоматично зупиняється.

## Локальний запуск

```bash
pip install -r requirements.txt
python scripts/fetch_news.py
python scripts/build.py          # результат у _site/
python -m http.server -d _site   # перегляд (шляхи розраховані на /blackseaway/)
python scripts/social_post.py preview   # що піде в соцмережі
```
