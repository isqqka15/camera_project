#include <SPI.h>
#include <MFRC522.h>

// Пин-коды для Arduino Uno
#define RST_PIN         9          // Пин RST на Arduino
#define SS_PIN          10         // Пин SDA на Arduino

MFRC522 mfrc522(SS_PIN, RST_PIN);  // Создаем объект MFRC522

void setup() {
  Serial.begin(115200);           // Скорость передачи данных 115200 бод
  while (!Serial);                // Ожидание открытия Serial-порта
  
  SPI.begin();                    // Инициализация шины SPI
  mfrc522.PCD_Init();             // Инициализация модуля MFRC522
}

void loop() {
  // Проверяем, поднесена ли новая карта
  if (!mfrc522.PICC_IsNewCardPresent()) {
    return;
  }

  // Считываем данные карты
  if (!mfrc522.PICC_ReadCardSerial()) {
    return;
  }

  // Формируем UID карты в hex-формате
  String cardUID = "";
  for (byte i = 0; i < mfrc522.uid.size; i++) {
    if (mfrc522.uid.uidByte[i] < 0x10) {
      cardUID += "0";
    }
    cardUID += String(mfrc522.uid.uidByte[i], HEX);
  }
  cardUID.toUpperCase();

  // Отправляем JSON-строку в Serial-порт для нашего сервиса
  Serial.print("{\"rfid\":\"");
  Serial.print(cardUID);
  Serial.println("\"}");

  // Останавливаем считывание текущей карты, чтобы она не считывалась по кругу
  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();
  
  delay(1000); // Пауза 1 секунда перед следующим считыванием
}