# Despliegue en el VPS (junto a otros proyectos)

Stack: **gunicorn** (servidor de la app) bajo **pm2** (igual que tus otros proyectos)
+ **nginx** como proxy en un puerto propio. Los estáticos los sirve WhiteNoise
(no hay que configurar nginx para eso).

> Reemplaza `cobranzas.midominio.com` por tu dominio/subdominio y `8010` por un
> puerto libre (revisa los usados con `pm2 list` y `ss -tlnp`).

## 1. Subir el código (una sola vez, desde tu PC)

```bash
cd C:\Cobranzas\Cobranzas
git init
git add .
git commit -m "Sistema de cobranzas del condominio"
git branch -M main
git remote add origin <URL-de-tu-repo>
git push -u origin main
```

Verifica que `git status` NO liste `.env` ni `db.sqlite3` antes del commit.

## 2. En el VPS: clonar e instalar

```bash
ssh user2@169.58.107.24
mkdir -p ~/apps && cd ~/apps
git clone <URL-de-tu-repo> cobranzas
cd cobranzas

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. Configurar el entorno de producción

```bash
cp .env.example .env
# Genera una clave nueva:
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
nano .env
```

Deja el `.env` así (con tus valores):

```
SECRET_KEY=<la-clave-que-generaste>
DEBUG=False
ALLOWED_HOSTS=cobranzas.midominio.com
CSRF_TRUSTED_ORIGINS=https://cobranzas.midominio.com
```

## 4. Base de datos, admin y estáticos

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput
```

## 5. Levantar la app con pm2

```bash
pm2 start ./venv/bin/gunicorn --name cobranzas --interpreter none -- \
  Cobranzas.wsgi:application --bind 127.0.0.1:8010 --workers 3

pm2 save          # persiste el proceso para reinicios del server
pm2 logs cobranzas   # revisar que arrancó sin errores (Ctrl+C para salir)
```

## 6. nginx: exponer el dominio

Crea `/etc/nginx/sites-available/cobranzas`:

```nginx
server {
    listen 80;
    server_name cobranzas.midominio.com;

    client_max_body_size 10M;   # para subir el Excel

    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Actívalo y recarga:

```bash
sudo ln -s /etc/nginx/sites-available/cobranzas /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## 7. HTTPS (recomendado)

```bash
sudo certbot --nginx -d cobranzas.midominio.com
```

Listo: entra a `https://cobranzas.midominio.com` con tu usuario admin.

---

## Actualizar la app más adelante (redeploy)

```bash
cd ~/apps/cobranzas
git pull
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
pm2 restart cobranzas
```

## Notas
- La base de datos (`db.sqlite3`) vive solo en el VPS; no se sube al repo. Haz
  respaldos copiando ese archivo.
- Cada proyecto del VPS usa su propio puerto y su propio nombre en pm2, así no
  chocan entre sí.
