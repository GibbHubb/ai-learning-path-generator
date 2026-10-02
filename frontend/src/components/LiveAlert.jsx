// AP40 — an error message a screen reader actually announces.
//
// The role="alert" container is ALWAYS in the DOM and only its content
// changes. Screen readers watch live regions that exist when the page is read;
// a region that is mounted at the same moment as its text is announced
// unreliably (NVDA and VoiceOver differ), which is the failure this replaces:
// every error in the app used to render into a plain <div> nobody announced.
import React from 'react';

export default function LiveAlert({ message, className = 'error-message', style, icon = '⚠️' }) {
    return (
        <div role="alert" className={message ? className : undefined} style={message ? style : undefined}>
            {message ? <span>{icon ? `${icon} ` : ''}{message}</span> : null}
        </div>
    );
}
