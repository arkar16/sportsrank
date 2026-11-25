/* Render only the game the reader chose. Team marks and the headline font are served locally. */
(() => {
  const link = document.querySelector("#game-link");
  const save = document.querySelector("#save-card");
  const status = document.querySelector("#share-status");

  if (navigator.clipboard && window.isSecureContext) {
    const copy = document.createElement("button");
    copy.type = "button";
    copy.textContent = "Copy link";
    link.replaceWith(copy);
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(link.href);
        status.textContent = "Link copied.";
      } catch {
        copy.replaceWith(link);
        status.textContent = "Press and hold or right-click Game link to copy it.";
        link.focus();
      }
    });
  }

  function drawText(ctx, text, x, y, {size, width, color = "#252a23", face = '"PT Serif", Georgia, serif', align = "left"}) {
    ctx.fillStyle = color;
    ctx.textAlign = align;
    ctx.textBaseline = "middle";
    ctx.font = 'bold ' + size + 'px ' + face;
    while (size > 18 && ctx.measureText(text).width > width) {
      size -= 1;
      ctx.font = 'bold ' + size + 'px ' + face;
    }
    ctx.fillText(text, x, y, width);
  }

  async function makeImage() {
    const canvas = document.createElement("canvas");
    canvas.width = 1200;
    canvas.height = 630;
    const ctx = canvas.getContext("2d");
    const rows = [...document.querySelectorAll(".game-line tbody tr")];
    const images = rows.map(row => row.querySelector("img"));
    await Promise.allSettled([
      document.fonts.load('bold 64px "PT Serif"'),
      ...images.filter(Boolean).map(image => image.decode())
    ]);
    ctx.fillStyle = "#faf7ef";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#823b27";
    ctx.fillRect(0, 0, 1200, 9);
    drawText(ctx, "SportsRank", 52, 61, {size: 51, width: 325, color: "#823b27"});
    drawText(ctx, document.querySelector(".edition").textContent, 1148, 62,
      {size: 25, width: 745, align: "right", face: "Arial, sans-serif"});
    ctx.fillStyle = "#252a23";
    ctx.fillRect(52, 104, 1096, 3);
    ctx.fillRect(52, 112, 1096, 1);
    drawText(ctx, "CORS spread", 1148, 150,
      {size: 25, width: 260, align: "right", color: "#565b50", face: "Arial, sans-serif"});

    rows.forEach((row, i) => {
      const y = 235 + i * 149;
      const image = images[i];
      if (image && image.naturalWidth && image.naturalHeight) {
        const scale = Math.min(104 / image.naturalWidth, 104 / image.naturalHeight);
        const w = image.naturalWidth * scale;
        const h = image.naturalHeight * scale;
        ctx.drawImage(image, 104 - w / 2, y - h / 2, w, h);
      }
      const x = images.some(Boolean) ? 185 : 52;
      drawText(ctx, row.querySelector(".school").textContent, x, y - 10,
        {size: 60, width: 866 - x});
      drawText(ctx, row.querySelector("small").textContent, x, y + 41,
        {size: 25, width: 490, face: "Arial, sans-serif", color: "#565b50"});
      drawText(ctx, row.querySelector("td").textContent, 1148, y,
        {size: 72, width: 245, align: "right"});
      ctx.fillStyle = "#cfcbbb";
      ctx.fillRect(52, y + 75, 1096, 2);
    });
    ctx.fillStyle = "#195348";
    ctx.fillRect(0, 498, 1200, 132);
    drawText(ctx, "CORS pick", 52, 563,
      {size: 28, width: 195, color: "#faf7ef", face: "Arial, sans-serif"});
    drawText(ctx, document.querySelector(".pick strong").textContent, 1148, 563,
      {size: 57, width: 840, color: "#faf7ef", align: "right"});
    return new Promise((resolve, reject) => canvas.toBlob(blob => {
      if (blob) resolve(blob);
      else reject(new Error("Could not encode the spread image"));
    }, "image/png"));
  }

  if (!HTMLCanvasElement.prototype.toBlob) return;
  save.hidden = false;
  save.addEventListener("click", async () => {
    save.disabled = true;
    status.textContent = "Preparing image…";
    try {
      const url = URL.createObjectURL(await makeImage());
      const download = document.createElement("a");
      download.href = url;
      download.download = location.pathname.split("/").pop().replace(/\.html$/, ".png");
      document.body.append(download);
      download.click();
      download.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = "Image ready.";
    } catch {
      status.textContent = "Couldn’t save the image. Try again or copy the game link.";
    } finally {
      save.disabled = false;
    }
  });
})();
