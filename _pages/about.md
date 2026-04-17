---
permalink: /
title: "About"
author_profile: true
redirect_from: 
  - /about/
  - /about.html
---

<style>
/* Career Timeline Styles */
.career-timeline {
  position: relative;
  max-width: 900px;
  margin: 40px auto;
  padding: 20px 0;
}

.career-timeline::before {
  content: '';
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  top: 0;
  bottom: 0;
  width: 3px;
  background: linear-gradient(180deg, #494e52 0%, #999 100%);
}

.timeline-item {
  position: relative;
  margin: 30px 0;
  width: 50%;
  padding: 0 40px;
  box-sizing: border-box;
}

.timeline-item:nth-child(odd) {
  left: 0;
  text-align: right;
}

.timeline-item:nth-child(even) {
  left: 50%;
  text-align: left;
}

.timeline-item::before {
  content: '';
  position: absolute;
  top: 20px;
  width: 16px;
  height: 16px;
  background: #494e52;
  border: 3px solid #fff;
  border-radius: 50%;
  z-index: 1;
}

.timeline-item:nth-child(odd)::before {
  right: -8px;
}

.timeline-item:nth-child(even)::before {
  left: -8px;
}

.timeline-content {
  background: #f9f9f9;
  padding: 20px;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  border-left: 4px solid #494e52;
}

.timeline-item:nth-child(odd) .timeline-content {
  border-left: none;
  border-right: 4px solid #494e52;
}

.timeline-year {
  display: inline-block;
  padding: 4px 12px;
  background: #494e52;
  color: #fff;
  border-radius: 4px;
  font-size: 0.85em;
  font-weight: 600;
  margin-bottom: 10px;
}

.timeline-title {
  font-size: 1.1em;
  font-weight: 700;
  color: #333;
  margin: 0 0 5px 0;
}

.timeline-org {
  font-size: 0.95em;
  color: #494e52;
  font-weight: 600;
  margin-bottom: 8px;
}

.timeline-location {
  font-size: 0.85em;
  color: #666;
}

.timeline-desc {
  font-size: 0.9em;
  color: #555;
  margin-top: 10px;
  line-height: 1.5;
}

.timeline-icon {
  font-size: 1.5em;
  margin-bottom: 10px;
}

/* Current position highlight */
.timeline-item.current .timeline-content {
  background: #494e52;
  color: #fff;
}

.timeline-item.current .timeline-title,
.timeline-item.current .timeline-org,
.timeline-item.current .timeline-location,
.timeline-item.current .timeline-desc {
  color: #fff;
}

.timeline-item.current .timeline-year {
  background: #fff;
  color: #494e52;
}

/* Intro section */
.intro-section {
  text-align: center;
  max-width: 800px;
  margin: 0 auto 40px auto;
  padding: 20px;
}

.intro-section h2 {
  font-size: 1.4em;
  color: #333;
  margin-bottom: 15px;
}

.intro-section p {
  font-size: 1.05em;
  color: #555;
  line-height: 1.7;
}

.skills-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  margin-top: 20px;
}

.skill-tag {
  padding: 6px 14px;
  background: #f0f0f0;
  color: #494e52;
  border-radius: 20px;
  font-size: 0.85em;
  font-weight: 600;
}

/* Responsive */
@media (max-width: 768px) {
  .career-timeline::before {
    left: 20px;
  }
  
  .timeline-item {
    width: 100%;
    left: 0 !important;
    text-align: left !important;
    padding-left: 50px;
    padding-right: 20px;
  }
  
  .timeline-item::before {
    left: 12px !important;
    right: auto !important;
  }
  
  .timeline-content {
    border-left: 4px solid #494e52 !important;
    border-right: none !important;
  }
}
</style>

<div class="intro-section" style="text-align: left;">
  <p>
    <strong>Dr. Chandan Kumar Sahu</strong> is a Research Scientist at <strong>ABB Inc.</strong>, where he pushes the knowledge frontier as an early adopter of nascent technologies to make industrial systems smarter and safer. He systematically integrates Generative AI, NLP, and network analysis techniques with the rigor of mathematical modeling and first-principles. This hybrid approach enables him to solve critical challenges across cyber-physical systems, additive manufacturing, requirements engineering, information retrieval systems, and CAD modeling. Dr. Sahu is a seasoned researcher whose scholarly contributions have been highly cited and resulted in multiple patents. His expertise is further evidenced by multiple industry accolades and hackathon wins. He holds a PhD in Automotive Engineering from Clemson University.
  </p>
  
  <div class="skills-tags">
    <span class="skill-tag">Deep Learning</span>
    <span class="skill-tag">NLP</span>
    <span class="skill-tag">GenAI/LLMs</span>
    <span class="skill-tag">RAG</span>
    <span class="skill-tag">Agentic AI</span>
    <span class="skill-tag">Requirements Engineering</span>
    <span class="skill-tag">Additive Manufacturing</span>
    <span class="skill-tag">Graph Theory</span>
  </div>
</div>

## Career Journey

<div class="career-timeline">

  <div class="timeline-item current">
    <div class="timeline-content">
      <div class="timeline-icon">🔬</div>
      <span class="timeline-year">2025 — Present</span>
      <h3 class="timeline-title">Research Scientist</h3>
      <div class="timeline-org">ABB US Corporate Research Center</div>
      <div class="timeline-location">📍 Morrisville, NC, USA</div>
      <p class="timeline-desc">
        Leading Generative AI and Agentic Systems research. Developed MotorMaven (information retrieval system), DraftGen (agentic CAD drafting), and filed 2 patents on AI for industrial applications.
      </p>
    </div>
  </div>

  <div class="timeline-item">
    <div class="timeline-content">
      <div class="timeline-icon">🎓</div>
      <span class="timeline-year">2020 — 2024</span>
      <h3 class="timeline-title">PhD in Mechanical Engineering</h3>
      <div class="timeline-org">Clemson University — CU-ICAR</div>
      <div class="timeline-location">📍 Greenville, SC, USA</div>
      <p class="timeline-desc">
        Dissertation: Computational representation, analysis and verification of requirements of complex systems. Built ReqCity (NLP tool for requirements), developed physics-guided ML for additive manufacturing.
      </p>
    </div>
  </div>

  <div class="timeline-item">
    <div class="timeline-content">
      <div class="timeline-icon">🎓</div>
      <span class="timeline-year">2017 — 2020</span>
      <h3 class="timeline-title">Master of Science</h3>
      <div class="timeline-org">SUNY Buffalo</div>
      <div class="timeline-location">📍 Buffalo, NY, USA</div>
      <p class="timeline-desc">
        Thesis: Modeling cyber-physical systems with physics-guided machine learning. Focused on hybrid modeling approaches combining physics-based and data-driven methods.
      </p>
    </div>
  </div>

  <div class="timeline-item">
    <div class="timeline-content">
      <div class="timeline-icon">💼</div>
      <span class="timeline-year">2014 — 2017</span>
      <h3 class="timeline-title">Executive — New Model Development</h3>
      <div class="timeline-org">Honda Motorcycle & Scooter India</div>
      <div class="timeline-location">📍 Manesar, India</div>
      <p class="timeline-desc">
        Project Leader for new model development. Led cross-functional teams for regulatory compliance, managed knowledge graphs of requirements, and certified 7 new 2W models.
      </p>
    </div>
  </div>

  <div class="timeline-item">
    <div class="timeline-content">
      <div class="timeline-icon">🎓</div>
      <span class="timeline-year">2010 — 2014</span>
      <h3 class="timeline-title">Bachelor of Technology</h3>
      <div class="timeline-org">NIT Rourkela</div>
      <div class="timeline-location">📍 Rourkela, Odisha, India</div>
      <p class="timeline-desc">
        Mechanical Engineering with focus on computational methods. Merit certificate in Mathematics (Top 0.1% in AISSCE 2010).
      </p>
    </div>
  </div>

</div>

## Research Interests

My research focuses on bridging the gap between AI capabilities and real-world engineering applications:

- **Generative AI & LLMs**: Fine-tuning large language models for domain-specific applications, RAG systems, and agentic frameworks
- **Requirements Engineering**: NLP-based tools for automated requirements analysis, verification, and traceability
- **Physics-Guided Machine Learning**: Hybrid models that combine physical principles with data-driven approaches
- **Additive Manufacturing**: ML-based process monitoring, melt pool prediction, and quality control

## Recent News

- **2025**: Joined ABB Inc. as Research Scientist — Generative AI and Agentic Systems
- **2025**: ReqCity++ recognized as Top 35 Innovative Solutions in NCMS CTMA Technology Competition
- **2024**: ReqCity listed as Transformative Entry in NCMS Army Digital Transformation Challenge
- **2024**: Published in ASME JCISE and IDETC conferences

