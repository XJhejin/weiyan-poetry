'use strict';
// Download targets are rendered at build time and remain usable without JavaScript.
fetch('/release.json', {cache:'no-cache'}).then(response=>{
  if(!response.ok)throw new Error('Release metadata unavailable');
  return response.json();
}).then(release=>{
  for(const label of document.querySelectorAll('[data-size]')){
    const asset=release.assets[label.dataset.size];
    if(asset)label.textContent=`· ${(asset.bytes/1048576).toFixed(1)} MB`;
  }
  for(const link of document.querySelectorAll('.download-link')){
    const asset=release.assets[link.dataset.kind];
    if(asset)link.title=`SHA-256: ${asset.sha256}`;
  }
}).catch(()=>{});
