# syntax=docker/dockerfile:1.7

FROM golang:1.26.7-trixie AS keyfinder-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake g++ curl pkg-config libfftw3-dev libavformat-dev libavcodec-dev libswresample-dev libavutil-dev
WORKDIR /src
RUN mkdir libkeyfinder keyfinder-cli sources \
    && curl -fLsS https://codeload.github.com/mixxxdj/libkeyfinder/tar.gz/941e517ebf853c2153a8b9d6efcc0c729199aa0b -o sources/libkeyfinder.tgz \
    && echo "7b922bb7605d2ada93bafe54a766b4547f109277f46dc48c31d946afb11639dd  sources/libkeyfinder.tgz" | sha256sum -c - \
    && curl -fLsS https://codeload.github.com/evanpurkhiser/keyfinder-cli/tar.gz/8958d9219fda8a48952da365d19752e43ee81f63 -o sources/keyfinder-cli.tgz \
    && echo "be7b6a80b52b707ece2b621ddda46d48c60138630a534934949d88d2ce3a12a7  sources/keyfinder-cli.tgz" | sha256sum -c - \
    && tar -xzf sources/libkeyfinder.tgz -C libkeyfinder --strip-components=1 \
    && tar -xzf sources/keyfinder-cli.tgz -C keyfinder-cli --strip-components=1 \
    && cmake -S libkeyfinder -B libkeyfinder/build -DBUILD_TESTING=OFF -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release \
    && cmake --build libkeyfinder/build -j2 && cmake --install libkeyfinder/build \
    && cmake -S keyfinder-cli -B keyfinder-cli/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build keyfinder-cli/build -j2

FROM golang:1.26.7-trixie AS rhythm-builder
RUN apt-get update && apt-get install -y --no-install-recommends g++ make curl libsndfile1-dev
WORKDIR /src
RUN curl -fLsS https://codeload.github.com/c4dm/qm-dsp/tar.gz/e34a3cc188332ed7c33cd9257ef164de5b587191 -o qm-dsp.tgz \
    && echo "34d77740f926a73be1a996a2ed77c064e0555a346ea2734b91d1cfdf9a1ac215  qm-dsp.tgz" | sha256sum -c - \
    && mkdir qm-dsp && tar -xzf qm-dsp.tgz -C qm-dsp --strip-components=1 \
    && make -C qm-dsp -f build/general/Makefile.inc -j2 CXXFLAGS='-O2 -std=c++11 -DUSE_PTHREADS -I. -Iext/kissfft -Iext/kissfft/tools -Dkiss_fft_scalar=double' CFLAGS='-O2 -I. -Iext/kissfft -Iext/kissfft/tools -Dkiss_fft_scalar=double -DNO_BLAS_WRAP -DADD_ -Iext/clapack/include -Iext/cblas/include'
COPY src/qm_beats.cpp /src/qm_beats.cpp
RUN g++ -O2 -std=c++17 -Iqm-dsp qm_beats.cpp qm-dsp/libqm-dsp.a -lsndfile -lpthread -o qm-beats

FROM golang:1.26.7-trixie AS beatport-builder

ARG BEATPORTDL_COMMIT=878d3f61c5a98cffd43b9e0306b8cbc75488999e
ARG BEATPORTDL_ARCHIVE_SHA256=91f64963d51c0e358a4dcc595e8ef1b081f0e3e5a300e8ac93152a4cfade5bdc
ARG QOBUZDL_COMMIT=00f8ce21b276ddf9278f0a944a2f85de2421d98d
ARG QOBUZDL_ARCHIVE_SHA256=32d1effc81c4ee7772ea49ada289ec4724d901faf29d03306859d6d8080822a4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libtag-c-dev \
        libtag-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN curl --fail --location --silent --show-error \
        "https://github.com/unspok3n/beatportdl/archive/${BEATPORTDL_COMMIT}.tar.gz" \
        --output /tmp/beatportdl.tar.gz \
    && echo "${BEATPORTDL_ARCHIVE_SHA256}  /tmp/beatportdl.tar.gz" | sha256sum --check --strict \
    && tar --extract --gzip --file /tmp/beatportdl.tar.gz --strip-components=1 \
    && rm /tmp/beatportdl.tar.gz

RUN mkdir -p /out/qobuz-dl \
    && curl --fail --location --silent --show-error \
        "https://github.com/guyathomas/qobuz-dl/archive/${QOBUZDL_COMMIT}.tar.gz" \
        --output /tmp/qobuz-dl.tar.gz \
    && echo "${QOBUZDL_ARCHIVE_SHA256}  /tmp/qobuz-dl.tar.gz" | sha256sum --check --strict \
    && tar --extract --gzip --file /tmp/qobuz-dl.tar.gz \
        --strip-components=1 --directory=/out/qobuz-dl \
    && rm /tmp/qobuz-dl.tar.gz

RUN mkdir -p /src/cmd/musicdl-search
COPY src/beatport_search.go /src/cmd/musicdl-search/main.go

RUN CGO_ENABLED=1 CGO_LDFLAGS="-lstdc++" go test -mod=vendor ./...
RUN CGO_ENABLED=1 go build \
    -mod=vendor \
    -trimpath \
    -buildmode=pie \
    -ldflags="-s -w -linkmode external -extldflags '-lstdc++'" \
    -o /out/beatportdl \
    ./cmd/beatportdl
RUN CGO_ENABLED=0 go build \
    -mod=vendor \
    -trimpath \
    -ldflags="-s -w" \
    -o /out/beatport-search \
    ./cmd/musicdl-search


FROM python:3.13.15-slim-trixie AS runtime

ARG BEATPORTDL_COMMIT=878d3f61c5a98cffd43b9e0306b8cbc75488999e
ARG QOBUZDL_COMMIT=00f8ce21b276ddf9278f0a944a2f85de2421d98d

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libfftw3-double3 \
        libsndfile1 \
        chromium \
        libstdc++6 \
        libtag-c2 \
        libtag2 \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    --requirement /app/requirements.lock
COPY requirements-analysis.lock /app/requirements-analysis.lock
RUN python -m pip install --no-cache-dir --disable-pip-version-check --requirement /app/requirements-analysis.lock
COPY --from=keyfinder-builder /src/keyfinder-cli/build/keyfinder-cli /usr/local/bin/keyfinder-cli
COPY --from=keyfinder-builder /src/sources /usr/share/keyfinder-sources
COPY --from=keyfinder-builder /src/keyfinder-cli/LICENSE /usr/share/licenses/keyfinder-cli/LICENSE
COPY --from=rhythm-builder /src/qm-beats /usr/local/bin/qm-beats
COPY --from=rhythm-builder /src/qm-dsp.tgz /src/qm_beats.cpp /usr/share/qm-sources/
COPY --from=rhythm-builder /src/qm-dsp/COPYING /usr/share/licenses/qm-dsp/COPYING

COPY --from=beatport-builder /out/beatportdl /usr/local/bin/beatportdl
COPY --from=beatport-builder /out/beatport-search /usr/local/bin/beatport-search
COPY --from=beatport-builder /src/LICENSE /usr/share/licenses/beatportdl/LICENSE
COPY --from=beatport-builder /out/qobuz-dl /opt/qobuz-dl
COPY --from=beatport-builder /out/qobuz-dl/LICENSE /usr/share/licenses/qobuz-dl/LICENSE
COPY src/musicdl.py /app/musicdl.py
COPY src/webapp.py src/database.py /app/
COPY migrations /app/migrations
COPY tools/migrate_postgres.py /app/migrate_postgres.py
COPY src/mixer_analysis.py src/analyze_audio.py /app/
COPY src/playlist_sources.py /app/playlist_sources.py
COPY src/spotify_browser.py /app/spotify_browser.py
COPY src/catalog_matcher.py src/import_engine.py src/spotify_http.py export_playlist.py /app/
COPY src/audio_retention.py src/feedback.py /app/
COPY src/stream_audio.py /app/
COPY src/qobuz_search.py /app/qobuz_search.py
COPY web /app/web
RUN mkdir -p \
        /config \
        /downloads/beatport \
        /downloads/tiddl \
        /downloads/qobuz \
        /deliveries \
        /queue/pending/tiddl \
        /queue/pending/beatport \
        /queue/pending/qobuz \
        /queue/running/tiddl \
        /queue/running/beatport \
        /queue/running/qobuz \
        /queue/done/tiddl \
        /queue/done/beatport \
        /queue/done/qobuz \
        /queue/failed/tiddl \
        /queue/failed/beatport \
        /queue/failed/qobuz \
        /queue/logs/tiddl \
        /queue/logs/beatport \
        /queue/logs/qobuz \
        /queue/locks/tiddl \
        /queue/locks/beatport \
        /queue/locks/qobuz \
        /state/tiddl \
        /state/beatportdl \
        /state/qobuz/.config/qobuz-dl \
        /state/home

ENV BEATPORTDL_COMMIT=${BEATPORTDL_COMMIT} \
    HOME=/state/home \
    MUSICDL_CONFIG_DIR=/config \
    MUSICDL_QUEUE_DIR=/queue \
    MUSICDL_STATE_DIR=/state \
    PYTHONPATH=/opt/qobuz-dl \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIDDL_PATH=/state/tiddl \
    TIDDL_VERSION=3.4.4 \
    QOBUZDL_COMMIT=${QOBUZDL_COMMIT} \
    XDG_CONFIG_HOME=/state \
    XDG_STATE_HOME=/state

ENTRYPOINT ["python", "/app/musicdl.py"]
CMD ["worker", "tiddl"]
