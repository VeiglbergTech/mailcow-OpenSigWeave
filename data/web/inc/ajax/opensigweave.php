<?php
require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/prerequisites.inc.php';
protect_route(['admin']);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
  http_response_code(405);
  echo json_encode(['detail' => 'Method not allowed']);
  exit;
}

$csrf = $_SERVER['HTTP_X_CSRF_TOKEN'] ?? '';
$expected = $_SESSION['CSRF']['TOKEN'] ?? '';
if ($csrf === '' || $expected === '' || !hash_equals($expected, $csrf)) {
  http_response_code(403);
  echo json_encode(['detail' => 'Invalid CSRF token']);
  exit;
}

$input = json_decode(file_get_contents('php://input'), true);
if (!is_array($input)) {
  http_response_code(400);
  echo json_encode(['detail' => 'Invalid JSON request']);
  exit;
}

$method = strtoupper((string)($input['method'] ?? ''));
$path = (string)($input['path'] ?? '');
$allowed = false;
if ($path === '/admin/catalog' && $method === 'GET') {
  $allowed = true;
} elseif ($path === '/admin/domains' && $method === 'POST') {
  $allowed = true;
} elseif (preg_match('~^/admin/domains/[0-9]+$~', $path) && $method === 'PUT') {
  $allowed = true;
} elseif ($path === '/admin/signatures' && $method === 'POST') {
  $allowed = true;
} elseif (preg_match('~^/admin/signatures/[a-f0-9]{32}$~', $path) && in_array($method, ['PUT', 'DELETE'], true)) {
  $allowed = true;
}
if (!$allowed) {
  http_response_code(400);
  echo json_encode(['detail' => 'Unsupported administration operation']);
  exit;
}

$engineKey = getenv('OPENSIGWEAVE_ENGINE_KEY');
if (!is_string($engineKey) || strlen($engineKey) < 32) {
  http_response_code(503);
  echo json_encode(['detail' => 'OpenSigWeave engine key is not configured']);
  exit;
}

$body = '';
if ($method !== 'GET') {
  $body = json_encode($input['payload'] ?? new stdClass(), JSON_UNESCAPED_SLASHES);
  if ($body === false) {
    http_response_code(400);
    echo json_encode(['detail' => 'Unable to encode request']);
    exit;
  }
}

$handle = curl_init('http://opensigweave:8090' . $path);
curl_setopt_array($handle, [
  CURLOPT_CUSTOMREQUEST => $method,
  CURLOPT_RETURNTRANSFER => true,
  CURLOPT_CONNECTTIMEOUT => 3,
  CURLOPT_TIMEOUT => 20,
  CURLOPT_HTTPHEADER => [
    'Accept: application/json',
    'Content-Type: application/json',
    'X-Engine-Key: ' . $engineKey,
  ],
]);
if ($method !== 'GET') {
  curl_setopt($handle, CURLOPT_POSTFIELDS, $body);
}
$response = curl_exec($handle);
$status = (int)curl_getinfo($handle, CURLINFO_HTTP_CODE);
$error = curl_error($handle);
curl_close($handle);

if ($response === false) {
  http_response_code(502);
  echo json_encode(['detail' => 'OpenSigWeave engine is unavailable', 'error' => $error]);
  exit;
}
http_response_code($status > 0 ? $status : 502);
echo $response;
