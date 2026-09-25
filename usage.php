<?php
require_once 'payment.php';

// กำหนดค่า API Key และ Base URL ของท่าน
$apiKey = "NXG_live_YourApiKeyHere"; 
$baseUrl = "https://merchant.nexgenapi.xyz";

// สร้าง Instance
$nxg = new NexgenPaymentAPI($apiKey, $baseUrl);

// ตัวอย่างที่ 1: ตรวจสอบสถานะระบบ
$status = $nxg->checkSystemStatus();
echo "<pre>System Status: " . print_r($status, true) . "</pre>";

// ตัวอย่างที่ 2: สร้างลิงก์ QR Code พร้อมเพย์ (ยอดเงิน 100 บาท)
$qr = $nxg->generateQrCode("0891234567", 100.00);
echo '<img src="' . $qr['qr_image_url'] . '" alt="PromptPay QR Code">';

// ตัวอย่างที่ 3: เติมเงิน TrueMoney Voucher
/*
$tmnResult = $nxg->redeemTrueMoney("https://gift.truemoney.com/campaign/?v=xxxxxx", "0891234567");
print_r($tmnResult);
*/

// ตัวอย่างที่ 4: ตรวจสอบสลิปโอนเงิน
/*
$slipResult = $nxg->verifySlip("004600060000010103...", 50.00);
print_r($slipResult);
*/
?>
