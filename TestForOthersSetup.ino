#include <Wire.h>
#include <Adafruit_DRV2605.h>

#define TCA_ADDR 0x70

Adafruit_DRV2605 drv;

void tcaSelect(uint8_t channel) {
  if (channel > 7) return;

  Wire.beginTransmission(TCA_ADDR);
  Wire.write(1 << channel);
  Wire.endTransmission();
}

void activateMotor(uint8_t channel, bool isDash) {
  tcaSelect(channel);
  delay(5);

  if (!drv.begin()) {
    Serial.print("DRV2605 not found on channel ");
    Serial.println(channel);
    return;
  }

  drv.selectLibrary(1);
  drv.setMode(DRV2605_MODE_INTTRIG);

  if (isDash) {
    drv.setWaveform(0, 47);   // longer / stronger
  } else {
    drv.setWaveform(0, 1);    // short pulse
  }

  drv.setWaveform(1, 0);
  drv.go();

  if (isDash) {
    delay(600);
  } else {
    delay(200);
  }
}

void setup() {
  Serial.begin(9600);
  Wire.begin();
  Serial.println("Ready for SC0-SC4 commands");
}

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd.startsWith("SC") || cmd.startsWith("SD")) {
      int channel = cmd.substring(2).toInt();

      // Accept SC0, SC1, SC2, SC3, SC4
      if (channel >= 0 && channel <= 4) {
        bool isDash = cmd.startsWith("SD");

        Serial.print("Received command: ");
        Serial.println(cmd);

        activateMotor((uint8_t)channel, isDash);
      } else {
        Serial.print("Invalid channel: ");
        Serial.println(channel);
      }
    }
  }
}
