<?php
require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/prerequisites.inc.php';
require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/triggers.admin.inc.php';

protect_route(['admin']);
$_SESSION['return_to'] = $_SERVER['REQUEST_URI'];

$context = \Mailcow\Core\Context::fromSession($_SESSION);
$domain_access = new \Mailcow\OpenSigWeave\Service\DomainAccessService(
  new \Mailcow\OpenSigWeave\Repository\DomainRepository($pdo),
  $context
);
$engine = new \Mailcow\OpenSigWeave\Service\EngineClient();
$controller = new \Mailcow\OpenSigWeave\Controller\SignaturePageController(
  $domain_access,
  $engine,
  $context,
  $pdo
);
$template_data = $controller->build('/admin/opensigweave');

require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/header.inc.php';
$template = 'opensigweave.twig';
require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/footer.inc.php';
