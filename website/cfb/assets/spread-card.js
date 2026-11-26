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
    canvas.width = 960;
    canvas.height = 540;
    const ctx = canvas.getContext("2d");
    const rows = [...document.querySelectorAll(".game-line tbody tr")];
    const images = rows.map(row => row.querySelector("img"));
    await Promise.allSettled([
      document.fonts.load('bold 64px "PT Serif"'),
      ...images.filter(Boolean).map(image => image.decode())
    ]);
    ctx.fillStyle = "#faf7ef";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    // Keep the scoreboard visible even in a messaging client's narrower 3:2 crop.
    const left = 80;
    const right = canvas.width - left;
    const contentWidth = right - left;
    drawText(ctx, "sportsrank.top", left, 55, {size: 35, width: 242, color: "#823b27"});
    drawText(ctx, document.querySelector(".edition").textContent, right, 55,
      {size: 27, width: contentWidth - 264, align: "right", face: "Arial, sans-serif"});
    ctx.fillStyle = "#252a23";
    ctx.fillRect(left, 94, contentWidth, 2);

    const markSize = 72;
    const rankX = images.some(Boolean) ? left + markSize + 14 : left;
    ctx.font = "bold 32px Arial, sans-serif";
    const rankWidth = Math.max(...rows.map(row => ctx.measureText(row.querySelector(".rank").textContent).width));
    const nameX = rankWidth ? rankX + rankWidth + 12 : rankX;
    ctx.font = 'bold 40px "PT Serif", Georgia, serif';
    const recordWidth = Math.min(236, Math.max(...rows.map(row => ctx.measureText(row.querySelector("td").textContent).width)));
    rows.forEach((row, i) => {
      const y = 159 + i * 128;
      const image = images[i];
      if (image && image.naturalWidth && image.naturalHeight) {
        const scale = Math.min(markSize / image.naturalWidth, markSize / image.naturalHeight);
        const w = image.naturalWidth * scale;
        const h = image.naturalHeight * scale;
        ctx.drawImage(image, left + (markSize - w) / 2, y - h / 2, w, h);
      }
      drawText(ctx, row.querySelector(".rank").textContent, rankX, y,
        {size: 32, width: rankWidth || 1, color: "#565b50", face: "Arial, sans-serif"});
      drawText(ctx, row.querySelector(".school").textContent, nameX, y,
        {size: 51, width: right - recordWidth - 20 - nameX});
      drawText(ctx, row.querySelector("td").textContent, right, y,
        {size: 40, width: recordWidth, align: "right"});
      ctx.fillStyle = "#cfcbbb";
      ctx.fillRect(left, y + 64, contentWidth, 1);
    });
    ctx.fillStyle = "#195348";
    ctx.fillRect(0, 352, canvas.width, canvas.height - 352);
    const pick = document.querySelector(".pick strong");
    drawText(ctx, pick.dataset.imagePick || pick.textContent, left, 420,
      {size: 55, width: contentWidth, color: "#faf7ef"});
    drawText(ctx, document.querySelector(".game-details").textContent, left, 482,
      {size: 29, width: contentWidth, color: "#faf7ef", face: "Arial, sans-serif"});
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
