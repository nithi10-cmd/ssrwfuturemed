/*
  ==========================================================
   ถุงมือกายภาพบำบัด v3 - Arduino UNO R4 WiFi + บอร์ด K-Matrix Mini R4
   ควบคุมผ่าน BLE (ArduinoBLE) + มอเตอร์ M1 (MatrixMiniR4) แบบใช้ encoder
  ==========================================================

  สิ่งที่เปลี่ยนจาก v2
    1) ใช้ encoder ของมอเตอร์ (getDegrees) บอกตำแหน่งจริง แทนการจับเวลา
       - v2 กำมือด้วยกำลัง 80 แต่แบมือด้วยกำลัง 50 ในเวลาเท่ากัน ทำให้สายถูกดึงเข้า
         มากกว่าที่คืนออกทุกรอบ ยิ่งฝึกหลายรอบ มือยิ่งถูกกำแน่นขึ้นเรื่อยๆ
       - v3 กำไปถึงองศาเป้าหมาย แล้วแบกลับถึงตำแหน่ง 0 ทุกรอบ จึงไม่มีการสะสมคลาดเคลื่อน
       - ปรับความเร็วแล้วระยะกำยังเท่าเดิม (v2 เร่งความเร็วแล้วกำได้สั้นลง)
    2) ไม่มี delay() แล้ว: ตอนสั่ง STOP/PAUSE หรือตอนหลุดการเชื่อมต่อ มอเตอร์จะค่อยๆ
       กลับตำแหน่งแบมือ ส่วน BLE ก็ยังรับคำสั่งได้ตลอด รวมถึง ESTOP
    3) ระบบความปลอดภัย
       - ESTOP: เบรกมอเตอร์ทันที (จาก BLE, Serial หรือปุ่ม BTN_UP บนบอร์ด)
       - ตรวจจับแรงต้าน: ถ้านิ้วเกร็งต้านจนมอเตอร์หมุนไม่ไป จะหยุดดึงทันที แล้วแจ้ง STALL
       - กันหมุนเกินระยะ / หมุนผิดทิศ / ใช้เวลานานผิดปกติ จะหยุดและแจ้ง ERR
    4) ส่งตำแหน่งนิ้ว (POS) แบบเรียลไทม์ ส่งแรงที่กำได้สูงสุดในแต่ละรอบ (PEAK)
       และส่งแรงดันแบตเตอรี่ (BATT) กลับไปให้เว็บแอปแสดงผล
    5) คาลิเบรตระยะกำมือได้จากเว็บแอป (JOG / ZERO / SETMAX) ไม่ต้องแก้โค้ดใหม่

  ไลบรารีที่ต้องมี: ArduinoBLE, MatrixMiniR4

  ⚠️ ตั้งค่าครั้งแรก ให้ทดสอบโดยไม่สวมมือก่อนเสมอ
    1. แบมือให้สุด (หรือปล่อยสายให้หย่อนพอดี) แล้วส่ง ZERO
    2. กด JOG+ ทีละนิดจนกำมือได้ระดับที่ผู้ป่วยรับได้ แล้วส่ง SETMAX
    3. ถ้าขึ้น ERR:DIRECTION แปลว่า encoder นับกลับทิศกับมอเตอร์ ให้เปลี่ยน ENC_SIGN เป็น -1

  โปรโตคอลคำสั่ง (ข้อความ ASCII ผ่าน BLE หรือ Serial 115200 ก็ได้)
    START, PAUSE, RESUME, STOP, ESTOP, TGT:10, SPD:50, HOLD:2000,
    ROM:360 (ระยะกำเป็นองศามอเตอร์), JOG:+ / JOG:-, ZERO, SETMAX, SYNC

  ข้อความที่ส่งกลับ
    STARTED, PHASE:GRIP|HOLD|RELEASE|TWIST, ROUND:n, PEAK:pct, POS:pct,
    STALL:pct, DONE, RETURNING, RETURNED, PAUSED, RESUMED, STOPPED, ESTOP,
    ERR:<เหตุผล>, BATT:7.4, CFG:rom,target,speed,holdMs, STATE:phase,round,paused

  UUID (Nordic UART Service)
    Service: 6E400001-B5A3-F393-E0A9-E50E24DCCA9E
    RX     : 6E400002-B5A3-F393-E0A9-E50E24DCCA9E  (เขียนคำสั่งเข้ามา)
    TX     : 6E400003-B5A3-F393-E0A9-E50E24DCCA9E  (notify สถานะ)
*/

#include <ArduinoBLE.h>
#include <MatrixMiniR4.h>

// ---------- ค่าที่ปรับได้ ----------
const int  ENC_SIGN        = 1;     // ถ้า encoder นับกลับทิศกับมอเตอร์ (ขึ้น ERR:DIRECTION) ให้เปลี่ยนเป็น -1
const int  POS_TOLERANCE   = 8;     // ถือว่าถึงเป้าหมายเมื่อห่างไม่เกินกี่องศา
const int  SLOWDOWN_DEG    = 40;    // เริ่มลดความเร็วเมื่อเข้าใกล้เป้าหมายกี่องศา
const int  MIN_POWER       = 25;    // กำลังต่ำสุดที่มอเตอร์ยังหมุนได้
const int  TWIST_POWER     = 35;    // กำลังตอนคลายเอ็น
const int  JOG_POWER       = 30;    // กำลังตอนคาลิเบรต
const unsigned long JOG_MS        = 150;
const unsigned long STALL_MS      = 600;    // ไม่ขยับเกินเวลานี้ = มีแรงต้าน
const int  STALL_MIN_DEG          = 4;      // ต้องขยับอย่างน้อยกี่องศาจึงนับว่ายังเคลื่อนที่
const unsigned long MOVE_TIMEOUT  = 10000;  // จังหวะใดเกินเวลานี้ = ผิดปกติ
const int  OVERTRAVEL_DEG         = 60;     // หมุนเกินระยะกำสูงสุดกี่องศาแล้วต้องตัด
const int  WRONG_DIR_DEG          = 45;     // หมุนสวนทางเป้าหมายกี่องศาแล้วต้องตัด
const int  ROM_MIN = 30, ROM_MAX = 1440;

// ---------- ค่าที่ปรับจากเว็บได้ ----------
int  romDeg        = 360;   // ระยะกำมือ (องศาที่มอเตอร์หมุน จาก 0 = แบมือ)
int  twistDeg      = 60;    // ระยะหมุนเลย 0 ออกไปเพื่อคลายเอ็น
int  targetRounds  = 10;
int  speedPercent  = 50;
unsigned long holdMs = 2000; // เวลากำค้างไว้ที่ตำแหน่งสุด

// ---------- BLE ----------
BLEService uartService("6E400001-B5A3-F393-E0A9-E50E24DCCA9E");
BLECharacteristic rxChar("6E400002-B5A3-F393-E0A9-E50E24DCCA9E", BLEWrite | BLEWriteWithoutResponse, 40);
BLECharacteristic txChar("6E400003-B5A3-F393-E0A9-E50E24DCCA9E", BLENotify, 40);

// ---------- สถานะ ----------
enum Phase { IDLE, GRIP, HOLD, RELEASE, TWIST_OUT, TWIST_IN, RETURNING, JOG };
Phase phase = IDLE;

bool isRunning = false;
bool isPaused  = false;
bool afterReturnPaused = false;   // กลับตำแหน่งเสร็จแล้วจะค้างพักไว้ (PAUSE) หรือจบ (STOP)
int  roundsCompleted = 0;
int  roundPeakDeg = 0;

unsigned long phaseStart = 0;

// การเคลื่อนที่ปัจจุบัน
int  moveTarget = 0;
int  movePower  = 0;
int  progressPos = 0;
unsigned long progressTime = 0;

// ตำแหน่งที่อ่านล่าสุด
int  curPos = 0;
unsigned long lastPosRead = 0;
int  lastSentPct = -999;
unsigned long lastPosSent = 0;
unsigned long lastBattSent = 0;
unsigned long lastBtnRead = 0;
bool lastBtnUp = false;

String serialLine;

// =========================================================
void setup() {
  Serial.begin(115200);

  MiniR4.begin();
  MiniR4.PWR.setBattCell(2);
  MiniR4.M1.setPPR_RPM(545, 200);
  MiniR4.M1.setReverse(false);
  MiniR4.M1.resetCounter();
  motorBrake();

  if (!BLE.begin()) {
    Serial.println("เริ่มต้น BLE ไม่สำเร็จ");
    while (1);
  }
  BLE.setLocalName("SmartGlove");
  BLE.setAdvertisedService(uartService);
  uartService.addCharacteristic(rxChar);
  uartService.addCharacteristic(txChar);
  BLE.addService(uartService);

  rxChar.setEventHandler(BLEWritten, onRxWritten);
  BLE.setEventHandler(BLEConnected, onConnected);
  BLE.setEventHandler(BLEDisconnected, onDisconnected);

  BLE.advertise();
  Serial.println("BLE พร้อม (ชื่ออุปกรณ์: SmartGlove) — พิมพ์คำสั่งผ่าน Serial ได้ด้วย");
}

void loop() {
  BLE.poll();
  readSerial();
  readButtons();
  readPosition();
  updateMotion();
  sendTelemetry();
}

// =========================================================
// BLE / Serial
// =========================================================
void onConnected(BLEDevice central) {
  Serial.print("เชื่อมต่อกับ: ");
  Serial.println(central.address());
}

void onDisconnected(BLEDevice central) {
  Serial.println("อุปกรณ์ตัดการเชื่อมต่อ — คืนมือสู่ตำแหน่งแบมือ");
  if (isRunning || phase == JOG) stopTraining();
  BLE.advertise();
}

void onRxWritten(BLEDevice central, BLECharacteristic characteristic) {
  int len = min(rxChar.valueLength(), 40);
  char buf[41];
  memcpy(buf, rxChar.value(), len);
  buf[len] = '\0';
  String cmd(buf);
  cmd.trim();
  handleCommand(cmd);
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      serialLine.trim();
      if (serialLine.length()) handleCommand(serialLine);
      serialLine = "";
    } else if (serialLine.length() < 40) {
      serialLine += c;
    }
  }
}

// ปุ่ม BTN_UP บนบอร์ด = หยุดฉุกเฉิน (กดได้แม้ไม่มีโทรศัพท์)
void readButtons() {
  if (millis() - lastBtnRead < 50) return;
  lastBtnRead = millis();
  bool up = MiniR4.BTN_UP.getState();
  if (up && !lastBtnUp) emergencyStop();
  lastBtnUp = up;
}

void sendStatus(const String &msg) {
  if (BLE.connected()) txChar.writeValue(msg.c_str());
  Serial.println(msg);
}

void handleCommand(String cmd) {
  cmd.toUpperCase();
  Serial.print("คำสั่ง: ");
  Serial.println(cmd);

  if (cmd == "ESTOP") {
    emergencyStop();
  } else if (cmd == "START") {
    startTraining();
  } else if (cmd == "PAUSE") {
    if (isPaused) resumeTraining();   // คงพฤติกรรมเดิม: กด PAUSE ซ้ำ = เล่นต่อ
    else pauseTraining();
  } else if (cmd == "RESUME") {
    resumeTraining();
  } else if (cmd == "STOP") {
    stopTraining();
  } else if (cmd.startsWith("TGT:")) {
    targetRounds = max(1, (int)cmd.substring(4).toInt());
    sendConfig();
  } else if (cmd.startsWith("SPD:")) {
    speedPercent = constrain((int)cmd.substring(4).toInt(), 0, 100);
    sendConfig();
  } else if (cmd.startsWith("HOLD:")) {
    holdMs = constrain(cmd.substring(5).toInt(), 0L, 10000L);
    sendConfig();
  } else if (cmd.startsWith("ROM:")) {
    romDeg = constrain((int)cmd.substring(4).toInt(), ROM_MIN, ROM_MAX);
    sendConfig();
  } else if (cmd == "JOG:+" || cmd == "JOG:-") {
    jog(cmd.endsWith("+") ? 1 : -1);
  } else if (cmd == "ZERO") {
    if (isRunning) { sendStatus("ERR:BUSY"); return; }
    MiniR4.M1.resetCounter();
    curPos = 0;
    sendStatus("POS:0");
  } else if (cmd == "SETMAX") {
    if (isRunning) { sendStatus("ERR:BUSY"); return; }
    if (curPos < ROM_MIN) { sendStatus("ERR:ROM_TOO_SMALL"); return; }
    romDeg = constrain(curPos, ROM_MIN, ROM_MAX);
    sendConfig();
  } else if (cmd == "SYNC") {
    sendConfig();
    sendState();
  } else {
    sendStatus("ERR:UNKNOWN_CMD");
  }
}

void sendConfig() {
  sendStatus("CFG:" + String(romDeg) + "," + String(targetRounds) + "," +
             String(speedPercent) + "," + String(holdMs));
}

void sendState() {
  sendStatus("STATE:" + String(phaseName(phase)) + "," + String(roundsCompleted) +
             "," + String(isPaused ? 1 : 0));
}

const char *phaseName(Phase p) {
  switch (p) {
    case GRIP: return "GRIP";
    case HOLD: return "HOLD";
    case RELEASE: return "RELEASE";
    case TWIST_OUT:
    case TWIST_IN: return "TWIST";
    case RETURNING: return "RETURNING";
    case JOG: return "JOG";
    default: return "IDLE";
  }
}

// =========================================================
// ควบคุมการฝึก
// =========================================================
// กำลังมอเตอร์ตามความเร็วที่ตั้ง (ระยะกำเท่าเดิม แค่ไปถึงเร็วขึ้นหรือช้าลง)
int gripPower()    { return 35 + speedPercent / 2; }               // 35..85
int releasePower() { return max(MIN_POWER, gripPower() * 7 / 10); } // แบมือนุ่มกว่ากำ

void startTraining() {
  if (phase == JOG) motorBrake();
  isRunning = true;
  isPaused = false;
  roundsCompleted = 0;
  sendStatus("STARTED");
  beginGrip();
}

void pauseTraining() {
  if (!isRunning || isPaused || phase == RETURNING) return;
  isPaused = true;
  sendStatus("PAUSED");
  beginReturn(true);
}

void resumeTraining() {
  if (!isRunning || !isPaused) return;
  if (phase == RETURNING) { sendStatus("ERR:BUSY"); return; }
  isPaused = false;
  sendStatus("RESUMED");
  beginGrip();   // เริ่มจากกำมือใหม่เสมอ เพราะตอนพักมือถูกคืนไปแบมือแล้ว
}

void stopTraining() {
  isPaused = false;
  isRunning = true;   // ให้ updateMotion ทำงานต่อจนกลับตำแหน่งเสร็จ
  beginReturn(false);
}

void emergencyStop() {
  motorBrake();
  isRunning = false;
  isPaused = false;
  phase = IDLE;
  MiniR4.Buzzer.Tone(1800, 400);
  sendStatus("ESTOP");
}

void fault(const char *reason) {
  motorBrake();
  isRunning = false;
  isPaused = false;
  phase = IDLE;
  MiniR4.Buzzer.Tone(600, 600);
  sendStatus(String("ERR:") + reason);
}

void jog(int dir) {
  if (isRunning) { sendStatus("ERR:BUSY"); return; }
  phase = JOG;
  phaseStart = millis();
  MiniR4.M1.setPower(dir * JOG_POWER);
}

// ---------- เริ่มแต่ละจังหวะ ----------
void enterPhase(Phase p) {
  phase = p;
  phaseStart = millis();
  progressPos = curPos;
  progressTime = millis();
}

void startMove(Phase p, int target, int power) {
  enterPhase(p);
  moveTarget = target;
  movePower = power;
}

void beginGrip() {
  roundPeakDeg = 0;
  startMove(GRIP, romDeg, gripPower());
  sendStatus("PHASE:GRIP");
}

void beginReturn(bool thenPause) {
  afterReturnPaused = thenPause;
  sendStatus("RETURNING");
  int power = curPos > 0 ? releasePower() : TWIST_POWER;
  startMove(RETURNING, 0, power);
}

// =========================================================
// ลูปควบคุมมอเตอร์ (ไม่บล็อก)
// =========================================================
void readPosition() {
  if (millis() - lastPosRead < 15) return;
  lastPosRead = millis();
  curPos = ENC_SIGN * MiniR4.M1.getDegrees();
}

// คืนค่า true เมื่อถึงเป้าหมายแล้ว (และเบรกมอเตอร์แล้ว)
// คืนค่า false ระหว่างเคลื่อนที่; ถ้าติดขัดจะเรียก onStall()
bool stepMove() {
  int err = moveTarget - curPos;
  if (abs(err) <= POS_TOLERANCE) {
    motorBrake();
    return true;
  }
  int dir = err > 0 ? 1 : -1;

  // หมุนเลยเป้าหมายไปแล้ว (เช่นมือถูกผู้ป่วยดันช่วย) ถือว่าถึงแล้ว
  if ((dir > 0 && curPos > moveTarget) || (dir < 0 && curPos < moveTarget)) {
    motorBrake();
    return true;
  }

  // ลดความเร็วเมื่อใกล้ถึง เพื่อไม่ให้กระชากและไม่เลยเป้าหมาย
  int power = movePower;
  if (abs(err) < SLOWDOWN_DEG) {
    power = max(MIN_POWER, movePower * abs(err) / SLOWDOWN_DEG);
  }
  MiniR4.M1.setPower(dir * power);

  // ตรวจว่ามอเตอร์ยังขยับอยู่ไหม
  if (abs(curPos - progressPos) >= STALL_MIN_DEG) {
    // ถ้าขยับสวนทิศทางที่สั่งมาก = encoder กลับทิศ
    if ((curPos - progressPos) * dir < 0 && abs(curPos - progressPos) >= WRONG_DIR_DEG) {
      fault("DIRECTION");
      return false;
    }
    if ((curPos - progressPos) * dir > 0) {
      progressPos = curPos;
      progressTime = millis();
    }
  } else if (millis() - progressTime > STALL_MS) {
    // ติดอยู่ใกล้เป้าหมายมากแล้ว (กำลังต่ำสุดอาจไม่พอดันต่อ) ถือว่าถึงแล้ว
    if (abs(err) <= 3 * POS_TOLERANCE) {
      motorBrake();
      return true;
    }
    onStall();
    return false;
  }

  if (millis() - phaseStart > MOVE_TIMEOUT) fault("TIMEOUT");
  return false;
}

void onStall() {
  motorBrake();
  if (phase == GRIP) {
    // นิ้วเกร็งต้าน: ไม่ฝืนดึงต่อ กำค้างไว้ที่ตำแหน่งเท่าที่ไปได้
    sendStatus("STALL:" + String(posPercent()));
    enterPhase(HOLD);
    sendStatus("PHASE:HOLD");
  } else {
    // ขณะคืนสายแล้วติด แปลว่ากลไกติดขัด ต้องหยุดให้คนตรวจสอบ
    fault("STALL");
  }
}

void updateMotion() {
  // กันหมุนเกินระยะไม่ว่าอยู่จังหวะไหน (ยกเว้นตอนคาลิเบรต)
  if (phase != IDLE && phase != JOG && curPos > romDeg + OVERTRAVEL_DEG) {
    fault("OVERTRAVEL");
    return;
  }

  switch (phase) {
    case IDLE:
      break;

    case JOG:
      if (millis() - phaseStart >= JOG_MS) {
        motorBrake();
        phase = IDLE;
        sendStatus("POS:" + String(posPercent()));
      }
      break;

    case GRIP:
      roundPeakDeg = max(roundPeakDeg, curPos);
      if (stepMove()) {
        enterPhase(HOLD);
        sendStatus("PHASE:HOLD");
      }
      break;

    case HOLD:
      roundPeakDeg = max(roundPeakDeg, curPos);
      if (millis() - phaseStart >= holdMs) {
        startMove(RELEASE, 0, releasePower());
        sendStatus("PHASE:RELEASE");
      }
      break;

    case RELEASE:
      if (stepMove()) {
        startMove(TWIST_OUT, -twistDeg, TWIST_POWER);
        sendStatus("PHASE:TWIST");
      }
      break;

    case TWIST_OUT:
      if (stepMove()) startMove(TWIST_IN, 0, TWIST_POWER);
      break;

    case TWIST_IN:
      if (stepMove()) finishRound();
      break;

    case RETURNING:
      if (stepMove()) {
        phase = IDLE;
        sendStatus("RETURNED");
        if (!afterReturnPaused) {
          isRunning = false;
          sendStatus("STOPPED");
        }
      }
      break;
  }
}

void finishRound() {
  roundsCompleted++;
  sendStatus("ROUND:" + String(roundsCompleted));
  sendStatus("PEAK:" + String(constrain(roundPeakDeg * 100L / romDeg, 0L, 100L)));

  if (roundsCompleted >= targetRounds) {
    isRunning = false;
    phase = IDLE;
    MiniR4.Buzzer.Tone(1200, 300);
    sendStatus("DONE");
    return;
  }
  beginGrip();
}

// =========================================================
// ส่งข้อมูลกลับ
// =========================================================
int posPercent() {
  return (int)((long)curPos * 100L / romDeg);
}

void sendTelemetry() {
  unsigned long now = millis();
  if (BLE.connected() && phase != IDLE && now - lastPosSent >= 120) {
    int pct = posPercent();
    if (abs(pct - lastSentPct) >= 2) {
      lastSentPct = pct;
      lastPosSent = now;
      txChar.writeValue(("POS:" + String(pct)).c_str());
    }
  }
  if (now - lastBattSent >= 10000) {
    lastBattSent = now;
    float v = MiniR4.PWR.getBattVoltage();
    if (v > 0) sendStatus("BATT:" + String(v, 1));
  }
}

void motorBrake() {
  MiniR4.M1.setPower(0);
  MiniR4.M1.setBrake(true);
}
