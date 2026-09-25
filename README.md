<div align="center">
Nexgen Payment Gateway PHP SDK
![PHP Version](https://img.shields.io/badge/PHP-%3E%3D%207.4-777BB4?style=for-the-badge&logo=php&logoColor=white)
![API Version](https://img.shields.io/badge/API-v1.8.0-blue?style=for-the-badge&logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)
![Maintenance](https://img.shields.io/badge/Maintained%3F-Yes-emerald?style=for-the-badge)
PHP Client Library อย่างเป็นทางการสำหรับเชื่อมต่อ Nexgen Payment Gateway API  
รองรับการสร้าง PromptPay Dynamic/Static QR Code, ตรวจสอบสลิปธนาคารอัตโนมัติ, เติมเงิน TrueMoney Voucher และระบบความปลอดภัย IP Whitelisting
เริ่มต้นใช้งาน •
การติดตั้ง •
ตัวอย่างการใช้งาน •
API Reference •
ระบบความปลอดภัย
---
</div>
📌 คุณสมบัติเด่น (Features)
⚡ PromptPay QR Code: สร้าง QR Code พร้อมเพย์แบบระบุจำนวนเงินหรือกำหนดเอง ได้ไฟล์ภาพ PNG พร้อมใช้งาน
🧾 Slip Verification: ตรวจสอบความถูกต้องของสลิปโอนเงินผ่าน QR Code Payload ป้องกันสลิปซ้ำและสลิปปลอม
🎁 TrueMoney Voucher Redeem: ระบบแลกซองของขวัญทรูมันนี่เข้าบัญชีเบอร์เป้าหมายอัตโนมัติ
🛡️ IP Whitelisting & Shield: ควบคุมความปลอดภัยระดับ Token-based IP Access Control
📊 Activity & Audit Logs: ดูประวัติการยิงคำขอและปริมาณ Transaction ย้อนหลังได้ทันที
🔌 Zero External Dependencies: ใช้เพียง PHP Native `cURL` และ `json` ไม่จำเป็นต้องลง Composer Library เสริม
---
💻 ความต้องการของระบบ (Requirements)
PHP: เวอร์ชัน `7.4` ขึ้นไป (รองรับเต็มรูปแบบบน PHP 8.0, 8.1, 8.2, 8.3+)
PHP Extensions:
`ext-curl`
`ext-json`
---
🚀 การติดตั้ง (Installation)
วิธีที่ 1: ติดตั้งแบบ Manual (แนะนำสำหรับระบบทั่วไป)
ดาวน์โหลดไฟล์ `payment.php` มาไว้ในโปรเจกต์ของคุณ:
```bash
git clone https://github.com/your-username/nexgenapi-php.git
```
เรียกใช้ไฟล์ในสคริปต์ PHP ของคุณ:
```php
require_once __DIR__ . '/payment.php';
```
---
⚙️ การเริ่มต้นใช้งาน (Quick Setup)
สร้าง Instance ของคลาส `NexgenPaymentAPI` โดยระบุ `API Key` และ Base URL ของระบบ:
```php
<?php
require_once 'payment.php';

// นำ API Key ที่ได้จาก Dashboard ของคุณมาใส่ที่นี่
$apiKey = 'NXG_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxx';

// กำหนด Base URL ของ Gateway (ค่าเริ่มต้นคือ https://merchant.nexgenapi.xyz)
$gateway = new NexgenPaymentAPI($apiKey, 'https://merchant.nexgenapi.xyz');
```
---
📖 ตัวอย่างการใช้งาน (Quick Examples)
1. ตรวจสอบสถานะระบบ (System Health Check)
```php
$status = $gateway->checkSystemStatus();

if ($status['success']) {
    print_r($status['response']);
} else {
    echo "Error: " . $status['message'];
}
```
---
2. สร้าง PromptPay QR Code (Generate QR Code)
```php
// สร้าง QR Code รับเงิน 150 บาท สำหรับเบอร์พร้อมเพย์
$result = $gateway->generateQrCode('0891234567', 150.00);

if ($result['success']) {
    // นำ URL ไปแสดงผลในแท็ก HTML Image
    echo '<img src="' . htmlspecialchars($result['qr_image_url']) . '" alt="PromptPay QR">';
}
```
---
3. ตรวจสอบสลิปการโอนเงิน (Slip Verification)
สแกน QR Code บนสลิปธนาคารและส่งข้อมูลเข้าตรวจสอบ:
```php
$qrData = "0046000600000101030140226..."; // ข้อมูลที่อ่านได้จาก QR Code สลิป
$expectedAmount = 150.00;

$verify = $gateway->verifySlip($qrData, $expectedAmount);

if ($verify['success']) {
    $slip = $verify['response'];
    echo "สถานะ: " . $slip['status'] . PHP_EOL;
    echo "เลขอ้างอิง: " . $slip['data']['transRef'] . PHP_EOL;
    echo "ผู้โอน: " . $slip['data']['sender']['name'] . PHP_EOL;
} else {
    echo "ตรวจสอบสลิปไม่สำเร็จ: " . ($verify['response']['message'] ?? 'Unknown Error');
}
```
---
4. แลกซองของขวัญ TrueMoney Voucher (Redeem Voucher)
```php
$voucherUrl = 'https://gift.truemoney.com/campaign/?v=xxxxxx';
$targetPhone = '0891234567';

$redeem = $gateway->redeemTrueMoney($voucherUrl, $targetPhone);

if ($redeem['success']) {
    echo "เติมเงินสำเร็จ ยอดเงิน: " . $redeem['response']['amount'] . " บาท";
} else {
    echo "เติมเงินไม่สำเร็จ: " . ($redeem['response']['status']['message'] ?? 'Error');
}
```
---
5. ตรวจสอบประวัติการเรียกใช้งาน (API Activity Logs)
```php
// ดึงประวัติคำขอล่าสุด 50 รายการ
$logs = $gateway->getApiActivity(50);

if ($logs['success']) {
    foreach ($logs['response']['data'] as $log) {
        echo "[{$log['created_at']}] {$log['method']} {$log['path']} - Status: {$log['status_code']}" . PHP_EOL;
    }
}
```
---
🔒 ระบบความปลอดภัย (Security & IP Access)
API ของ Nexgen ใช้สถาปัตยกรรมความปลอดภัยแบบ Zero-Trust:
IP Auto-Detection: เมื่อยิงคำขอครั้งแรกจาก Server IP ระบบจะบันทึกสถานะเป็น `PENDING`
IP Whitelisting: ต้องได้รับการอนุมัติ (Approved) จากผู้ดูแลระบบ หรืออนุญาตผ่าน Dashboard ก่อน จึงจะเข้าถึง Endpoint การเงินได้
Protected Header: ทุกคำขอจะส่งผ่าน HTTPS พร้อม Header:
```http
   X-API-Key: NXG_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
---
🛠️ โครงสร้าง Response มาตรฐาน
```json
{
  "status": 200,
  "message": "Operation completed successfully",
  "data": { ... }
}
```
กรณีเกิดข้อผิดพลาด (Error Handling)
```json
{
  "status": 403,
  "error": "IP_ACCESS_PENDING",
  "message": "IP access is pending administrator approval",
  "ip": "203.0.113.195"
}
```
---
🤝 การสนับสนุนและการติดต่อ (Support)
Portal: https://merchant.nexgenapi.xyz
API Version: `v1.8.0`
Issue Tracker: สำหรับแจ้งปัญหาการใช้งาน กรุณาเปิด Ticket ผ่านระบบหลังบ้านของท่าน
---
<div align="center">
  <p>© Nexgen Payment Gateway. All rights reserved.</p>
</div>
