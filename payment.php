<?php

/**
 * Nexgen Payment Gateway API Client for PHP
 * รองรับการเชื่อมต่อ API: https://merchant.nexgenapi.xyz
 */
class NexgenPaymentAPI
{
    private string $baseUrl;
    private string $apiKey;
    private int $timeout;

    /**
     * Constructor สำหรับกำหนดค่าพื้นฐาน
     * 
     * @param string $apiKey API Key ของท่าน (เช่น NXG_live_xxxxxx)
     * @param string $baseUrl URL ของ API Gateway (ค่าเริ่มต้น: https://merchant.nexgenapi.xyz)
     * @param int $timeout กำหนดเวลา Timeout (วินาที)
     */
    public function __construct(string $apiKey, string $baseUrl = 'https://merchant.nexgenapi.xyz', int $timeout = 30)
    {
        $this->apiKey = trim($apiKey);
        $this->baseUrl = rtrim($baseUrl, '/');
        $this->timeout = $timeout;
    }

    /**
     * ฟังก์ชันกลางสำหรับยิง HTTP Request ด้วย cURL
     */
    private function sendRequest(string $method, string $endpoint, ?array $data = null): array
    {
        $url = $this->baseUrl . '/' . ltrim($endpoint, '/');
        $ch = curl_init();

        $headers = [
            "X-API-Key: " . $this->apiKey,
            "Accept: application/json"
        ];

        $options = [
            CURLOPT_URL => $url,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_ENCODING => "",
            CURLOPT_MAXREDIRS => 10,
            CURLOPT_TIMEOUT => $this->timeout,
            CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_1_1,
            CURLOPT_CUSTOMREQUEST => strtoupper($method),
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
        ];

        if (strtoupper($method) === 'POST') {
            if ($data !== null) {
                $payload = json_encode($data);
                $headers[] = "Content-Type: application/json";
                $options[CURLOPT_POSTFIELDS] = $payload;
            }
        } elseif (strtoupper($method) === 'GET' && !empty($data)) {
            $url .= '?' . http_build_query($data);
            $options[CURLOPT_URL] = $url;
        }

        $options[CURLOPT_HTTPHEADER] = $headers;

        curl_setopt_array($ch, $options);

        $response = curl_exec($ch);
        $err = curl_error($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        if ($err) {
            return [
                "success" => false,
                "status_code" => 500,
                "error" => "CURL_ERROR",
                "message" => "cURL Error: " . $err
            ];
        }

        $decoded = json_decode($response, true);
        
        // กรณี Response ไม่ใช่ JSON (เช่น กรณีขอรูปภาพ QR Code PNG)
        if (json_last_error() !== JSON_ERROR_NONE) {
            return [
                "success" => $httpCode >= 200 && $httpCode < 300,
                "status_code" => $httpCode,
                "raw_body" => $response
            ];
        }

        return [
            "success" => $httpCode >= 200 && $httpCode < 300,
            "status_code" => $httpCode,
            "response" => $decoded
        ];
    }

    /**
     * 1. ตรวจสอบสถานะระบบ API (Root Endpoint)
     */
    public function checkSystemStatus(): array
    {
        return $this->sendRequest('GET', '/');
    }

    /**
     * 2. สร้าง PromptPay QR Code (คืนค่าเป็น Binary Data ของรูปภาพ PNG หรือ URL สำหรับแสดงผล)
     * 
     * @param string $promptpay เบอร์โทรศัพท์ หรือเลขพร้อมเพย์
     * @param float|null $amount จำนวนเงิน (ถ้ามี)
     */
    public function generateQrCode(string $promptpay, ?float $amount = null): array
    {
        $params = ['promptpay' => $promptpay];
        if ($amount !== null) {
            $params['amount'] = $amount;
        }
        
        // Endpoint นี้คืนค่ากลับมาเป็นรูปภาพ PNG
        $url = $this->baseUrl . '/qrpayment?' . http_build_query($params);
        
        return [
            "success" => true,
            "qr_image_url" => $url,
            "note" => "คุณสามารถนำ URL นี้ไปใส่ในแท็ก <img src=\"...\"> ได้ทันทีโดยต้องแนบ Header หรือเปิดใช้งานผ่านระบบที่รองรับ"
        ];
    }

    /**
     * 3. ระบบ Redeem ซองของขวัญ TrueMoney Voucher
     * 
     * @__param string $giftLink ลิงก์ซองของขวัญ TrueMoney
     * @__param string $phone เบอร์รับเงินทรูมันนี่
     */
    public function redeemTrueMoney(string $giftLink, string $phone): array
    {
        $payload = [
            "gift" => $giftLink,
            "phone" => $phone
        ];

        return $this->sendRequest('POST', '/redeem/truemoney', $payload);
    }

    /**
     * 4. ตรวจสอบสลิปการโอนเงิน (Slip Verification)
     * 
     * @param string $qrcode ข้อมูล Payload จาก QR Code บนสลิปธนาคาร
     * @__param float $amount จำนวนเงินที่โอน
     */
    public function verifySlip(string $qrcode, float $amount): array
    {
        $payload = [
            "qrcode" => $qrcode,
            "amount" => $amount
        ];

        return $this->sendRequest('POST', '/slipverify', $payload);
    }

    /**
     * 5. ดึงประวัติการเรียกใช้งาน API (Activity Logs)
     * 
     * @param int $limit จำนวนรายการที่ต้องการดึง (สูงสุด 500)
     */
    public function getApiActivity(int $limit = 100): array
    {
        return $this->sendRequest('GET', '/v1/api/activity', ['limit' => $limit]);
    }

    /**
     * 6. ดึงรายการ IP Access ควบคุมความปลอดภัย
     */
    public function listIpAccess(): array
    {
        return $this->sendRequest('GET', '/v1/api/access/ip');
    }
}
