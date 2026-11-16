<?php
// File size is limited by Nginx site to 10M
// To speed things up, we do not include prerequisites
header('Content-Type: text/plain');
require_once "vars.inc.php";
// Do not show errors, we log to using error_log
ini_set('error_reporting', 0);

// Init database
$dsn = $database_type . ":unix_socket=" . $database_sock . ";dbname=" . $database_name;
$opt = [
    PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES   => false,
];
try {
  $pdo = new PDO($dsn, $database_user, $database_pass, $opt);
}
catch (PDOException $e) {
  error_log("FOOTER: " . $e . PHP_EOL);
  http_response_code(501);
  exit;
}

if (!function_exists('getallheaders')) {
  function getallheaders() {
    if (!is_array($_SERVER)) {
      return array();
    }
    $headers = array();
    foreach ($_SERVER as $name => $value) {
      if (substr($name, 0, 5) == 'HTTP_') {
        $headers[str_replace(' ', '-', ucwords(strtolower(str_replace('_', ' ', substr($name, 5)))))] = $value;
      }
    }
    return $headers;
  }
}

$headers = getallheaders();
$domain = strtolower(trim($headers['Domain'] ?? ''));
$username = strtolower(trim($headers['Username'] ?? ''));
$from = strtolower(trim($headers['From'] ?? ''));

$empty_footer = json_encode(array(
  'html' => '',
  'plain' => '',
  'skip_replies' => 0,
  'vars' => array()
));

if ($domain === '' || $username === '' || $from === '') {
  echo $empty_footer;
  exit;
}

error_log("FOOTER: checking for domain " . $domain . ", user " . $username . " and address " . $from . PHP_EOL);

try {
  // Resolve alias domains before looking up domain policy.
  $stmt = $pdo->prepare("SELECT `target_domain` FROM `alias_domain` WHERE `alias_domain` = :alias_domain");
  $stmt->execute([':alias_domain' => $domain]);
  $alias_domain = $stmt->fetch(PDO::FETCH_ASSOC);
  $target_domain = $alias_domain ? $alias_domain['target_domain'] : $domain;

  // Existing Mailcow footer remains the fallback when OpenSigWeave is disabled
  // or does not return a signature.
  $stmt = $pdo->prepare("SELECT `plain`, `html`, `mbox_exclude`, `alias_domain_exclude`, `skip_replies`
    FROM `domain_wide_footer` WHERE `domain` = :domain");
  $stmt->execute([':domain' => $target_domain]);
  $footer = $stmt->fetch(PDO::FETCH_ASSOC);

  $stmt = $pdo->prepare("SELECT `custom_attributes` FROM `mailbox` WHERE `username` = :username");
  $stmt->execute([':username' => $username]);
  $mailbox = $stmt->fetch(PDO::FETCH_ASSOC);
  $custom_attributes = $mailbox['custom_attributes'] ?? '{}';
  if (empty($custom_attributes)) {
    $custom_attributes = '{}';
  }
}
catch (Exception $e) {
  error_log("FOOTER: " . $e->getMessage() . PHP_EOL);
  http_response_code(502);
  exit;
}

$excluded = false;
if (is_array($footer)) {
  $mailbox_exclusions = json_decode($footer['mbox_exclude'] ?? '[]', true);
  $alias_exclusions = json_decode($footer['alias_domain_exclude'] ?? '[]', true);
  if (is_array($mailbox_exclusions) && in_array($from, $mailbox_exclusions, true)) {
    $excluded = true;
  }
  if (is_array($alias_exclusions) && in_array($domain, $alias_exclusions, true)) {
    $excluded = true;
  }
}

// OpenSigWeave is called only by the existing Mailcow footer pipeline. It does
// not inject MIME itself; Rspamd remains the sole message-body modifier.
if (!$excluded && getenv('OPENSIGWEAVE_ENABLED') === 'y' && function_exists('curl_init')) {
  $decoded_attributes = json_decode($custom_attributes, true);
  if (!is_array($decoded_attributes)) {
    $decoded_attributes = [];
  }
  $osw_attributes = $decoded_attributes['opensigweave'] ?? [];
  if (!is_array($osw_attributes)) {
    $osw_attributes = [];
  }

  $profile = [
    'principal_id' => (string)($osw_attributes['principal_id'] ?? ''),
    'first_name' => (string)($osw_attributes['first_name'] ?? ''),
    'last_name' => (string)($osw_attributes['last_name'] ?? ''),
    'title' => (string)($osw_attributes['title'] ?? ''),
    'phone' => (string)($osw_attributes['phone'] ?? $osw_attributes['telephoneNumber'] ?? ''),
    'groups' => is_array($osw_attributes['groups'] ?? null) ? $osw_attributes['groups'] : [],
    'claims' => is_array($osw_attributes['claims'] ?? null) ? $osw_attributes['claims'] : [],
    'attributes' => is_array($osw_attributes['attributes'] ?? null) ? $osw_attributes['attributes'] : [],
  ];

  $engine_key = getenv('OPENSIGWEAVE_ENGINE_KEY') ?: '';
  if (strlen($engine_key) >= 32) {
    $payload = json_encode([
      'authenticated_user' => $username,
      'envelope_sender' => $from,
      'target_domain' => $target_domain,
      'profile' => $profile,
    ]);

    if ($payload !== false) {
      $curl = curl_init('http://opensigweave:8090/internal/signature');
      curl_setopt_array($curl, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $payload,
        CURLOPT_HTTPHEADER => [
          'Content-Type: application/json',
          'X-Engine-Key: ' . $engine_key,
        ],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT_MS => 500,
        CURLOPT_TIMEOUT_MS => 2000,
        CURLOPT_PROTOCOLS => CURLPROTO_HTTP,
      ]);
      $engine_body = curl_exec($curl);
      $engine_status = (int)curl_getinfo($curl, CURLINFO_HTTP_CODE);
      $engine_error = curl_error($curl);
      curl_close($curl);

      if ($engine_status === 200 && is_string($engine_body)) {
        $engine_result = json_decode($engine_body, true);
        if (is_array($engine_result) && !empty($engine_result['html'])) {
          echo json_encode([
            'html' => $engine_result['html'],
            'plain' => is_string($engine_result['plain'] ?? null) ? $engine_result['plain'] : '',
            'skip_replies' => !empty($engine_result['inject_on_replies']) ? 0 : 1,
            'vars' => '{}',
          ]);
          exit;
        }
      }
      if ($engine_error !== '') {
        error_log("OPENSIGWEAVE: internal engine unavailable; using Mailcow footer fallback (" . $engine_error . ")");
      } elseif ($engine_status !== 200 && $engine_status !== 0) {
        error_log("OPENSIGWEAVE: engine returned HTTP " . $engine_status . "; using Mailcow footer fallback");
      }
    } else {
      error_log("OPENSIGWEAVE: engine key is not configured; using Mailcow footer fallback");
    }
  }
}

if ($excluded || !is_array($footer)) {
  echo $empty_footer;
  exit;
}

// Return the original Mailcow footer and mailbox variables as fallback.
$footer["vars"] = $custom_attributes;
echo json_encode($footer);
