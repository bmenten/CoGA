import React, { useEffect, useRef } from "react";
import * as d3 from "d3";

interface PedRow {
  fid: string;
  iid: string;
  pid: string;
  mid: string;
  sex: string;
  phen: string;
}

interface Props {
  rows: PedRow[];
}

const NODE_SIZE = 20;
// Increase spacing to reduce label overlap
const GEN_VERTICAL_GAP = 100;
const SIBLING_HORIZONTAL_GAP = 100;
const COUPLE_GAP = 50;
const SIBLING_LINE_OFFSET = 20;

const Pedigree: React.FC<Props> = ({ rows }) => {
  const svgRef = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const rowMap = new Map(rows.map((r) => [r.iid, r]));

    const getGeneration = (iid: string): number => {
      const r = rowMap.get(iid);
      if (!r) return 0;
      let g = 0;
      if (r.pid && r.pid !== "0") g = Math.max(g, getGeneration(r.pid) + 1);
      if (r.mid && r.mid !== "0") g = Math.max(g, getGeneration(r.mid) + 1);
      return g;
    };

    // group children by their parents
    const familyMap = new Map<
      string,
      { father: string | null; mother: string | null; children: string[] }
    >();
    const familiesByParent = new Map<
      string,
      { father: string | null; mother: string | null; children: string[] }
    >();
    rows.forEach((r) => {
      const father = r.pid !== "0" ? r.pid : null;
      const mother = r.mid !== "0" ? r.mid : null;
      if (father || mother) {
        const key = `${father ?? "0"}_${mother ?? "0"}`;
        if (!familyMap.has(key)) {
          familyMap.set(key, { father, mother, children: [] });
        }
        familyMap.get(key)!.children.push(r.iid);
        if (father) familiesByParent.set(father, familyMap.get(key)!);
        if (mother) familiesByParent.set(mother, familyMap.get(key)!);
      }
    });

    // determine required width for each family recursively so that
    // descendant subtrees have enough horizontal room and do not overlap
    const familyWidths = new Map<string, number>();
    const calcFamilyWidth = (
      fatherId: string | null,
      motherId: string | null,
    ): number => {
      const key = `${fatherId ?? "0"}_${motherId ?? "0"}`;
      if (familyWidths.has(key)) return familyWidths.get(key)!;
      const children = familyMap.get(key)?.children || [];
      if (!children.length) {
        familyWidths.set(key, SIBLING_HORIZONTAL_GAP);
        return SIBLING_HORIZONTAL_GAP;
      }
      let width = 0;
      children.forEach((cid, idx) => {
        const cf = familiesByParent.get(cid);
        const childWidth = cf
          ? calcFamilyWidth(cf.father, cf.mother)
          : SIBLING_HORIZONTAL_GAP;
        width += childWidth;
        if (idx < children.length - 1) width += SIBLING_HORIZONTAL_GAP;
      });
      familyWidths.set(key, width);
      return width;
    };

    // precompute widths for all families
    familyMap.forEach((fam) => {
      calcFamilyWidth(fam.father ?? null, fam.mother ?? null);
    });

    const positions = new Map<string, { x: number; y: number }>();

    const layoutFamily = (
      fatherId: string | null,
      motherId: string | null,
      generation: number,
      leftX: number,
    ) => {
      const key = `${fatherId ?? "0"}_${motherId ?? "0"}`;
      const famWidth = familyWidths.get(key) || SIBLING_HORIZONTAL_GAP;
      const yParent = generation * GEN_VERTICAL_GAP + 50;

      const children = familyMap.get(key)?.children || [];
      let childLeft = leftX;
      const childY = yParent + GEN_VERTICAL_GAP;
      children.forEach((cid) => {
        const cf = familiesByParent.get(cid);
        const cKey = cf
          ? `${cf.father ?? "0"}_${cf.mother ?? "0"}`
          : null;
        const childWidth = cKey
          ? familyWidths.get(cKey)!
          : SIBLING_HORIZONTAL_GAP;
        const childCenter = childLeft + childWidth / 2;
        positions.set(cid, { x: childCenter, y: childY });
        if (cf) layoutFamily(cf.father, cf.mother, generation + 1, childLeft);
        childLeft += childWidth + SIBLING_HORIZONTAL_GAP;
      });

      // Anchor parents above the span of their children so single parents sit over the correct child.
      const centerX =
        children.length > 0
          ? (positions.get(children[0])!.x +
              positions.get(children[children.length - 1])!.x) /
            2
          : leftX + famWidth / 2;

      if (fatherId && motherId) {
        const fatherX = centerX - COUPLE_GAP / 2;
        const motherX = centerX + COUPLE_GAP / 2;
        positions.set(fatherId, { x: fatherX, y: yParent });
        positions.set(motherId, { x: motherX, y: yParent });
      } else {
        const parentId = fatherId || motherId!;
        positions.set(parentId, { x: centerX, y: yParent });
      }
    };

    // layout root families (those whose parents are not present)
    let xCursor = 50;
    familyMap.forEach((fam) => {
      const fRow = fam.father ? rowMap.get(fam.father) : null;
      const mRow = fam.mother ? rowMap.get(fam.mother) : null;
      const fParents =
        fam.father && fRow && (fRow.pid !== "0" || fRow.mid !== "0");
      const mParents =
        fam.mother && mRow && (mRow.pid !== "0" || mRow.mid !== "0");
      if (!fParents && !mParents) {
        const key = `${fam.father ?? "0"}_${fam.mother ?? "0"}`;
        const width = familyWidths.get(key) || SIBLING_HORIZONTAL_GAP;
        layoutFamily(fam.father ?? null, fam.mother ?? null, 0, xCursor);
        xCursor += width + SIBLING_HORIZONTAL_GAP;
      }
    });

    // place individuals not covered by any family
    rows.forEach((r) => {
      if (!positions.has(r.iid)) {
        const g = getGeneration(r.iid);
        positions.set(r.iid, {
          x: xCursor,
          y: g * GEN_VERTICAL_GAP + 50,
        });
        xCursor += SIBLING_HORIZONTAL_GAP;
      }
    });

    let minX = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    positions.forEach((p) => {
      minX = Math.min(minX, p.x);
      maxX = Math.max(maxX, p.x);
      maxY = Math.max(maxY, p.y);
    });
    const offsetX = -minX + 50;
    positions.forEach((p) => {
      p.x += offsetX;
    });
    const width = maxX - minX + 100;
    const height = maxY + 50;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("width", width).attr("height", height);

    // draw connections according to pedigree conventions
    familyMap.forEach((fam) => {
      const fatherPos = fam.father ? positions.get(fam.father) : null;
      const motherPos = fam.mother ? positions.get(fam.mother) : null;
      const parentPos = fatherPos || motherPos;
      if (!parentPos) return;
      const parentY = parentPos.y;
      const parentBottomY = parentY + NODE_SIZE / 2;
      const centerX = fatherPos && motherPos
        ? (fatherPos.x + motherPos.x) / 2
        : parentPos.x;
      if (fatherPos && motherPos) {
        svg
          .append("line")
          .attr("x1", fatherPos.x)
          .attr("y1", fatherPos.y)
          .attr("x2", motherPos.x)
          .attr("y2", motherPos.y)
          .attr("stroke", "black");
      }
      const children = fam.children;
      if (!children.length) return;
      if (children.length === 1) {
        const childPos = positions.get(children[0])!;
        const childTopY = childPos.y - NODE_SIZE / 2;
        svg
          .append("line")
          .attr("x1", centerX)
          .attr("y1", parentBottomY)
          .attr("x2", centerX)
          .attr("y2", childTopY)
          .attr("stroke", "black");
      } else {
        const siblingLineY = parentBottomY + SIBLING_LINE_OFFSET;
        svg
          .append("line")
          .attr("x1", centerX)
          .attr("y1", parentBottomY)
          .attr("x2", centerX)
          .attr("y2", siblingLineY)
          .attr("stroke", "black");
        const firstChildPos = positions.get(children[0])!;
        const lastChildPos = positions.get(children[children.length - 1])!;
        svg
          .append("line")
          .attr("x1", firstChildPos.x)
          .attr("y1", siblingLineY)
          .attr("x2", lastChildPos.x)
          .attr("y2", siblingLineY)
          .attr("stroke", "black");
        children.forEach((cid) => {
          const childPos = positions.get(cid)!;
          const childTopY = childPos.y - NODE_SIZE / 2;
          svg
            .append("line")
            .attr("x1", childPos.x)
            .attr("y1", siblingLineY)
            .attr("x2", childPos.x)
            .attr("y2", childTopY)
            .attr("stroke", "black");
        });
      }
    });

    rows.forEach((r) => {
      const pos = positions.get(r.iid);
      if (!pos) return;
      const fill = r.phen === "2" ? "black" : "white";
      const stroke = "black";
      if (r.sex === "1") {
        svg
          .append("rect")
          .attr("x", pos.x - NODE_SIZE / 2)
          .attr("y", pos.y - NODE_SIZE / 2)
          .attr("width", NODE_SIZE)
          .attr("height", NODE_SIZE)
          .attr("fill", fill)
          .attr("stroke", stroke);
      } else if (r.sex === "2") {
        svg
          .append("circle")
          .attr("cx", pos.x)
          .attr("cy", pos.y)
          .attr("r", NODE_SIZE / 2)
          .attr("fill", fill)
          .attr("stroke", stroke);
      } else {
        const diamondPath =
          `M${pos.x} ${pos.y - NODE_SIZE / 2} ` +
          `L${pos.x + NODE_SIZE / 2} ${pos.y} ` +
          `L${pos.x} ${pos.y + NODE_SIZE / 2} ` +
          `L${pos.x - NODE_SIZE / 2} ${pos.y} Z`;
        svg
          .append("path")
          .attr("d", diamondPath)
          .attr("fill", fill)
          .attr("stroke", stroke);
      }
      svg
        .append("text")
        .attr("x", pos.x)
        .attr("y", pos.y + NODE_SIZE)
        .attr("text-anchor", "middle")
        // Slightly reduce label font size
        .attr("font-size", 8)
        .text(r.iid);
    });
  }, [rows]);

  return (
    <svg
      ref={svgRef}
      className="pedigree-svg"
    />
  );
};

export default Pedigree;
