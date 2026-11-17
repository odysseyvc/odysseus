# The image Odysseus runs in on a server. Only the program itself goes in:
# no keys, no data, no tests, no site.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY odysseus ./odysseus

# The program runs as an ordinary user, not as root.
RUN useradd --create-home app && mkdir -p /data

# Everything Odysseus must keep (memory, schedule, log) lives in /data, which
# the host mounts from a disk that outlives the machine.
ENV ODYSSEUS_DATA_DIR=/data

# A mounted disk arrives owned by root, so its ownership is handed to the
# program's user at start-up, and then the program is started as that user.
CMD ["sh", "-c", "chown -R app:app /data && exec runuser -u app -- python -W ignore -m odysseus"]
