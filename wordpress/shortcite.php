<?php

/*
Plugin Name: ShortCite
Description: Shortcode for &lt;cite&gt; tag
Author: durrell@innocence.com
Version: 0.1
 */

// Add Shortcode
function cite_shortcode( $atts , $content = null ) {
    return '<cite>' . $content . '</cite>';
}
add_shortcode( 'cite', 'cite_shortcode' );

?>
