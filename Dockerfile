# set_stereo_default with everything it needs: Python, ffmpeg, MKVToolNix and tqdm.
#
#   docker build -t set-stereo-default .
#   docker run --rm -it -v "/path/to/videos:/videos" set-stereo-default --dry-run
#
# Alpine, because Debian's ffmpeg package pulls in about 600 MB of graphics
# libraries (LLVM, Mesa) the script never uses: this image is about 310 MB,
# against about 890 MB on debian:13-slim.
FROM alpine:3.24

LABEL org.opencontainers.image.title="set-stereo-default" \
      org.opencontainers.image.description="Make the stereo audio track play by default, across your whole video library." \
      org.opencontainers.image.source="https://github.com/tronyx/Set-Stereo-Default" \
      org.opencontainers.image.licenses="MIT"

RUN apk add --no-cache ffmpeg mkvtoolnix python3 py3-tqdm

COPY set_stereo_default.py /app/set_stereo_default.py

# PYTHONUNBUFFERED shows each line as it happens, even without -t (docker logs, cron).
# LANG makes non-English file and track names read and print correctly.
# SET_STEREO_DEFAULT_IN_DOCKER makes the script's advice use docker commands.
ENV PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    SET_STEREO_DEFAULT_IN_DOCKER=1

WORKDIR /app

# The script handles SIGTERM itself, so docker stop removes half-written files
# and exits cleanly (code 143) without needing --init.
ENTRYPOINT ["python3", "/app/set_stereo_default.py"]
CMD ["--help"]
