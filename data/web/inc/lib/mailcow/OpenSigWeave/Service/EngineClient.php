<?php

declare(strict_types=1);

namespace Mailcow\OpenSigWeave\Service;

use RuntimeException;

final class EngineClient
{
    private string $baseUrl;
    private string $apiKey;

    public function __construct(?string $baseUrl = null, ?string $apiKey = null)
    {
        $baseUrl = $baseUrl ?? (getenv('OPENSIGWEAVE_ENGINE_URL') ?: 'http://opensigweave:8090');
        $apiKey = $apiKey ?? (getenv('OPENSIGWEAVE_ENGINE_KEY') ?: '');

        $parts = parse_url($baseUrl);
        if (
            !is_array($parts)
            || !in_array($parts['scheme'] ?? '', ['http', 'https'], true)
            || empty($parts['host'])
            || isset($parts['user'])
            || isset($parts['pass'])
            || isset($parts['query'])
            || isset($parts['fragment'])
        ) {
            throw new RuntimeException('OpenSigWeave engine URL is invalid');
        }
        if (strlen($apiKey) < 32) {
            throw new RuntimeException('OpenSigWeave engine key is not configured');
        }
        if (!function_exists('curl_init')) {
            throw new RuntimeException('PHP cURL extension is unavailable');
        }

        $this->baseUrl = rtrim($baseUrl, '/');
        $this->apiKey = $apiKey;
    }

    /** @return array<string, mixed> */
    private function request(string $method, string $path, ?array $payload = null): array
    {
        $url = $this->baseUrl . '/' . ltrim($path, '/');
        $handle = curl_init($url);
        if ($handle === false) {
            throw new RuntimeException('Could not initialize OpenSigWeave request');
        }

        $headers = [
            'Accept: application/json',
            'X-Engine-Key: ' . $this->apiKey,
        ];
        $options = [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CUSTOMREQUEST => strtoupper($method),
            CURLOPT_CONNECTTIMEOUT => 2,
            CURLOPT_TIMEOUT => 8,
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_MAXREDIRS => 0,
            CURLOPT_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS,
            CURLOPT_REDIR_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS,
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
        ];

        if ($payload !== null) {
            $json = json_encode($payload, JSON_THROW_ON_ERROR);
            $options[CURLOPT_POSTFIELDS] = $json;
            $options[CURLOPT_HTTPHEADER][] = 'Content-Type: application/json';
        }

        curl_setopt_array($handle, $options);
        $body = curl_exec($handle);
        $status = (int)curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
        $curlError = curl_error($handle);
        curl_close($handle);

        if ($body === false) {
            throw new RuntimeException('OpenSigWeave engine is unavailable' . ($curlError !== '' ? ': ' . $curlError : ''));
        }

        try {
            $decoded = json_decode((string)$body, true, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException $exception) {
            throw new RuntimeException('OpenSigWeave engine returned invalid JSON', 0, $exception);
        }

        if ($status < 200 || $status >= 300) {
            $detail = is_array($decoded) && isset($decoded['detail']) && is_string($decoded['detail'])
                ? $decoded['detail']
                : 'Engine request failed';
            throw new EngineClientException($status, $detail);
        }
        if (!is_array($decoded)) {
            throw new RuntimeException('OpenSigWeave engine returned an invalid response');
        }

        return $decoded;
    }

    /** @return array<string, mixed> */
    public function listDomainSignatures(string $domain): array
    {
        return $this->request('GET', 'internal/admin/domains/' . rawurlencode($domain) . '/signatures');
    }

    /** @return array<string, mixed> */
    public function getDomainSignature(string $domain, string $signatureId): array
    {
        return $this->request(
            'GET',
            'internal/admin/domains/' . rawurlencode($domain) . '/signatures/' . rawurlencode($signatureId)
        );
    }

    /** @param array<string, mixed> $payload
     *  @return array<string, mixed>
     */
    public function createDomainSignature(string $domain, array $payload): array
    {
        return $this->request(
            'POST',
            'internal/admin/domains/' . rawurlencode($domain) . '/signatures',
            $payload
        );
    }

    /** @return array<string, mixed> */
    public function updateDomainSignature(string $domain, string $signatureId, bool $isActive): array
    {
        return $this->request(
            'PUT',
            'internal/admin/domains/' . rawurlencode($domain) . '/signatures/' . rawurlencode($signatureId),
            ['is_active' => $isActive]
        );
    }

    /** @param array<string, mixed> $payload
     *  @return array<string, mixed>
     */
    public function addDomainSignatureVersion(string $domain, string $signatureId, array $payload): array
    {
        return $this->request(
            'POST',
            'internal/admin/domains/' . rawurlencode($domain) . '/signatures/' . rawurlencode($signatureId) . '/versions',
            $payload
        );
    }

    /** @return array<string, mixed> */
    public function listDomainSignatureVersions(string $domain, string $signatureId): array
    {
        return $this->request(
            'GET',
            'internal/admin/domains/' . rawurlencode($domain) . '/signatures/' . rawurlencode($signatureId) . '/versions'
        );
    }

    /** @return array<string, mixed> */
    public function restoreDomainSignatureVersion(string $domain, string $signatureId, string $versionId): array
    {
        return $this->request(
            'POST',
            'internal/admin/domains/' . rawurlencode($domain) . '/signatures/' . rawurlencode($signatureId)
                . '/restore/' . rawurlencode($versionId)
        );
    }
}

final class EngineClientException extends RuntimeException
{
    public function __construct(
        public readonly int $httpStatus,
        string $message
    ) {
        parent::__construct($message);
    }
}
