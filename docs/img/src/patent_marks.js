// Marker and box positions on pages/patent_front.png (US 7,587,347 B2, front page, 1600 x 1278 px).
// The scan has no text layer, so these are ink boxes printed by measure_patent.py
// (x = first to last ink column of the words, top = cap top of the line).
(function () {
  const T = (top, x0, x1) => ({ x0: x0 - 5, y0: top - 5, x1: x1 + 5, y1: top + 20 }); // a title line
  window.PATENT_MARKS = [
    // (54) title, six lines
    T(311, 267, 684), T(337, 267, 729), T(363, 268, 761), T(389, 268, 742), T(415, 267, 664), T(441, 268, 718),
    // (75) Inventors: "Kenneth C. Griffin" (ink 377-562; the comma after it starts at 566)
    { x0: 372, y0: 487, x1: 565, y1: 513 },
    // (73) Assignee: "Citadel Investment Group, L.L.C." (ink 376-712; the comma after it starts at 716)
    { x0: 371, y0: 563, x1: 714, y1: 590 },
  ];
  // red box: the (75) Inventors and (73) Assignee block, labels included (ink 267-743 x 492-614)
  window.PATENT_BOX = { x0: 256, y0: 481, x1: 755, y1: 626 };
})();
