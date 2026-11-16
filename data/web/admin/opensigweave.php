<?php
require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/prerequisites.inc.php';
require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/triggers.admin.inc.php';

protect_route(['admin']);
require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/header.inc.php';

$_SESSION['return_to'] = $_SERVER['REQUEST_URI'];
$template = 'opensigweave.twig';
$template_data = [
  'opensigweave_enabled' => getenv('OPENSIGWEAVE_ENABLED') === 'y',
];

$js_minifier->add('/web/js/site/opensigweave.js');
require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/footer.inc.php';
