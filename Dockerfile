FROM python:3.10-bookworm

RUN apt-get update && \
    apt-get install -y git gettext libmariadb-dev libpq-dev locales libmemcached-dev build-essential \
            supervisor \
            sudo \
            locales \
            --no-install-recommends && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    dpkg-reconfigure locales && \
    locale-gen C.UTF-8 && \
    /usr/sbin/update-locale LANG=C.UTF-8 && \
    mkdir /etc/pretalx && \
    mkdir /data && \
    mkdir /public && \
    groupadd -g 999 pretalxuser && \
    useradd -r -u 999 -g pretalxuser -d /pretalx -ms /bin/bash pretalxuser && \
    echo 'pretalxuser ALL=(ALL) NOPASSWD:SETENV: /usr/bin/supervisord' >> /etc/sudoers

ENV LC_ALL=C.UTF-8


# Layers are ordered from least to most frequently changed, so that editing a
# plugin only reruns the plugin steps at the end, not the pretalx install.
COPY pretalx/pyproject.toml /pretalx
COPY pretalx/src /pretalx/src

RUN pip3 install -U pip "setuptools<81" wheel typing && \
    pip3 install -e /pretalx/[mysql,postgres,redis] && \
    pip3 install pylibmc && \
    pip3 install gunicorn

# Full static build for pretalx itself, including the npm frontend
RUN apt-get update && \
    apt-get install -y nodejs npm && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    python3 -m pretalx rebuild

# Plugins: afterwards only their static files need collecting and compressing
COPY plugins /plugins
# --no-deps: never let pip swap the pretalx installed above for a PyPI release
RUN pip3 install --no-deps /plugins/pretalx-pretix-sso && \
    python3 -m pretalx makemigrations && \
    python3 -m pretalx migrate && \
    python3 -m pretalx collectstatic --noinput && \
    python3 -m pretalx compress

COPY deployment/docker/pretalx.bash /usr/local/bin/pretalx
COPY deployment/docker/supervisord.conf /etc/supervisord.conf

RUN chmod +x /usr/local/bin/pretalx && \
    cd /pretalx/src && \
    rm -f pretalx.cfg && \
    chown -R pretalxuser:pretalxuser /pretalx /data /public && \
    rm -f /pretalx/src/data/.secret

USER pretalxuser
VOLUME ["/etc/pretalx", "/data", "/public"]
EXPOSE 80
ENTRYPOINT ["pretalx"]
CMD ["all"]
