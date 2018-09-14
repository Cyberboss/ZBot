FROM python:3

WORKDIR /usr/src/app

EXPOSE 8080

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mv config.example.json config.json

CMD [ "python", "zbot" ]
