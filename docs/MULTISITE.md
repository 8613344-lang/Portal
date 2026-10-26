# Несколько сайтов на одном сервере и одном IP

Общий Caddy принимает запросы на HTTPS 443 и направляет их по доменному имени в нужное приложение. Порт 80 служит для получения сертификатов и перенаправления HTTP → HTTPS. Приложения и PostgreSQL не публикуют свои порты на сервере. Каждому независимому сайту Portal выделены отдельные база, файлы, настройки, аккаунты и внутренняя сеть.

Например, `qicko.ru` и ваш второй домен имеют A-записи на один публичный IPv4. Внутри сервера они направляются на `qicko-app:8000` и `stories-app:8000`. Для посетителя оба сайта открываются обычными HTTPS-адресами без номера порта. Второй домен в примерах — `stories.example`; его обязательно замените своим реальным доменом. DNS и рабочие домены этот репозиторий автоматически не изменяет.

Подготовлены два файла:

- `deploy/multisite/compose.gateway.yaml` — один общий Caddy для всех сайтов;
- `deploy/multisite/compose.site.yaml` — независимый стек одного Portal без собственного публичного gateway.

Общий прокси может обслуживать и другие приложения: подключите их веб-контейнер к сети `portal_edge` с уникальным именем и добавьте отдельный блок домена с `reverse_proxy`.

## 1. DNS и сервер

Для каждого домена создайте A-запись с одним и тем же IP сервера. Если добавляете AAAA, IPv6 должен действительно вести на этот сервер. Откройте TCP 80 и 443 в маршрутизаторе и firewall, перенаправьте их на сервер. На этих портах должен работать только общий Caddy.

Используйте актуальный Docker Engine с Compose v2 или новее. Файлы проекта не требуют host networking или Docker socket внутри контейнеров.

Справки: [Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https), [reverse proxy](https://caddyserver.com/docs/quick-starts/reverse-proxy). HTTPS и выбор сертификата используют доменное имя клиента; каждый домен получает свой сертификат.

## 2. Общий gateway

Пусть первая копия проекта расположена в `/srv/qicko/Portal`. Выполните из этой папки:

~~~bash
cp deploy/multisite/.env.gateway.example deploy/multisite/.env.gateway
cp deploy/multisite/sites/qicko.caddy.example deploy/multisite/sites/qicko.caddy
~~~

В `.env.gateway` укажите рабочий email администратора для сертификатов. В `sites/qicko.caddy` должен быть ваш домен и правильный сетевой адрес:

~~~caddyfile
qicko.ru {
    import portal_app qicko-app:8000
}
~~~

Запустите gateway; он создаст общую сеть `portal_edge`:

~~~bash
docker compose --env-file deploy/multisite/.env.gateway -f deploy/multisite/compose.gateway.yaml -p portal-edge up -d
~~~

Пока приложения ещё нет, домен может отвечать 502. Это устраняется следующим шагом. Данные сертификатов находятся в постоянных volumes gateway; сохраняйте их при обновлении.

## 3. Первый сайт

Создайте `.env` из `.env.example` в корне этой копии. Установите как минимум:

~~~dotenv
DEBUG=0
SITE_KEY=qicko
DOMAIN=qicko.ru
SITE_URL=https://qicko.ru
ALLOWED_HOSTS=localhost,127.0.0.1,qicko.ru
CSRF_TRUSTED_ORIGINS=https://qicko.ru
BEHIND_PROXY=1
SECRET_KEY=ЗАМЕНИТЕ_УНИКАЛЬНЫМ_СЛУЧАЙНЫМ_КЛЮЧОМ
POSTGRES_DB=portal
POSTGRES_USER=portal
POSTGRES_PASSWORD=ЗАМЕНИТЕ_СЛУЧАЙНЫМ_ПАРОЛЕМ
ANALYTICS_GEOIP_PATH=geoip/city.mmdb
~~~

Задайте SMTP и Telegram согласно обычной инструкции развёртывания. `SITE_KEY` — уникальное имя латиницей в нижнем регистре, например `qicko` или `stories`; оно задаёт адрес `qicko-app` в общей сети.

~~~bash
docker compose --env-file .env -f deploy/multisite/compose.site.yaml -p qicko up -d --build
docker compose --env-file .env -f deploy/multisite/compose.site.yaml -p qicko exec app python manage.py createsuperuser
docker compose --env-file .env -f deploy/multisite/compose.site.yaml -p qicko exec app python manage.py update_geoip
docker compose --env-file .env -f deploy/multisite/compose.site.yaml -p qicko exec app python manage.py run_analytics --once
~~~

Миграции и collectstatic выполняются стартом приложения. Для нового сайта создаётся новая пустая база. Сетевое имя upstream в Caddy должно совпадать с `${SITE_KEY}-app`.

## 4. Второй независимый сайт

Создайте отдельную копию проекта, например `/srv/stories/Portal`. В её `.env` задайте `SITE_KEY=stories`, реальный второй домен, его `SITE_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, собственные SECRET_KEY, пароль PostgreSQL и реквизиты уведомлений.

Из корня второй копии:

~~~bash
docker compose --env-file .env -f deploy/multisite/compose.site.yaml -p stories up -d --build
docker compose --env-file .env -f deploy/multisite/compose.site.yaml -p stories exec app python manage.py createsuperuser
docker compose --env-file .env -f deploy/multisite/compose.site.yaml -p stories exec app python manage.py update_geoip
~~~

В **первой копии**, где запущен общий gateway, создайте `deploy/multisite/sites/stories.caddy`:

~~~caddyfile
stories.example {
    import portal_app stories-app:8000
}
~~~

Замените `stories.example` настоящим доменом. Затем из корня первой копии проверьте и перечитайте конфигурацию:

~~~bash
docker compose --env-file deploy/multisite/.env.gateway -f deploy/multisite/compose.gateway.yaml -p portal-edge exec gateway caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose --env-file deploy/multisite/.env.gateway -f deploy/multisite/compose.gateway.yaml -p portal-edge exec gateway caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
~~~

Так добавляются следующие домены. Gateway не запускается повторно в каждой копии. Его `sites/` — единственный список маршрутов на этом сервере.

## 5. Переход существующего сайта с обычного compose.yaml

Сначала сохраните резервные копии PostgreSQL и приватных файлов по основной инструкции. Не запускайте новую базу вместо старой.

Узнайте **прежнее имя Compose-проекта** командой `docker compose ls`. Если существующий стек назывался `portal`, используйте **`-p portal`** и после перехода. Не меняйте имена и пути его volumes. `SITE_KEY=qicko` при этом остаётся сетевым адресом и не обязан совпадать с именем проекта.

Подготовьте файлы общего gateway и маршруты. Из корня существующей копии:

~~~bash
docker compose stop gateway
docker compose --env-file deploy/multisite/.env.gateway -f deploy/multisite/compose.gateway.yaml -p portal-edge up -d
docker compose --env-file .env -f deploy/multisite/compose.site.yaml -p portal up -d --build
~~~

Последняя команда сохраняет старые `portal_postgres_data` и `portal_private_media`. Прежний gateway остановлен и больше не занимает 443; удаление его сертификатов не требуется. Общий gateway использует собственное постоянное хранилище и получает сертификаты для настроенных доменов. Здесь не используется `down -v`: эта команда удаляет volumes с данными.

После проверки DNS/HTTPS войдите в прежнюю админку и убедитесь, что книги и пользователи сохранились. Если старые volumes имеют нестандартные имена или были внешними, сначала явно сопоставьте их в копии Compose-файла; приведённые команды рассчитаны на стандартные volumes проекта.

## 6. IP посетителя, обновления и проверки

Caddy перезаписывает `X-Real-IP` и `X-Forwarded-Proto`; произвольный заголовок посетителя не используется как доверенный IP. Для GeoIP/статистики задайте доверенную подсеть прокси. Её можно узнать:

~~~bash
docker network inspect portal_edge --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
~~~

Если подсеть отличается от стандартной `172.16.0.0/12`, добавьте её в `ANALYTICS_TRUSTED_PROXIES` каждого сайта и перезапустите приложение. Не указывайте `0.0.0.0/0`. При внешнем CDN IP будет адресом CDN, пока отдельно не настроена доверенная цепочка прокси.

Для обновления каждого сайта используйте **его** копию и **его прежнее `-p`**:

~~~bash
git pull --ff-only
docker compose --env-file .env -f deploy/multisite/compose.site.yaml -p qicko up -d --build
~~~

Проверьте оба домена с телефона и компьютера: свой каталог, вход, админка, QR/PDF со своим доменом. Аккаунты, сессии и статистика разных стеков независимы. В каждом стеке свой video_worker и analytics_worker. Для резервного копирования сохраняются база и private_media каждого проекта; GeoIP можно загрузить повторно.

На машине разработки проверены TLS-маршрутизация двух локальных доменов на один порт с проверкой сертификатов и разрешённые Compose-конфигурации двух независимых проектов. Полный контейнерный запуск не выполнялся, так как Docker daemon недоступен. Публичный DNS и сервер владельца не изменялись.
