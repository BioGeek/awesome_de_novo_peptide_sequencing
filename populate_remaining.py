#!/usr/bin/env python3
"""Populate denovo.db with all remaining articles from related_literature.csv."""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "denovo.db")


def get_or_create_country(cur, name):
    cur.execute("SELECT id FROM country WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO country (name) VALUES (?)", (name,))
    return cur.lastrowid


def get_or_create_city(cur, name, country_id):
    cur.execute("SELECT id FROM city WHERE name = ? AND country_id = ?", (name, country_id))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO city (name, country_id) VALUES (?, ?)", (name, country_id))
    return cur.lastrowid


def get_or_create_affiliation(cur, name, department, city_id, country_id):
    if department:
        cur.execute(
            "SELECT id FROM affiliation WHERE name = ? AND department = ?",
            (name, department),
        )
    else:
        cur.execute(
            "SELECT id FROM affiliation WHERE name = ? AND department IS NULL",
            (name,),
        )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO affiliation (name, department, city_id, country_id) VALUES (?, ?, ?, ?)",
        (name, department, city_id, country_id),
    )
    return cur.lastrowid


def get_or_create_author(cur, name, email=None):
    cur.execute("SELECT id FROM author WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        if email:
            cur.execute("UPDATE author SET email = ? WHERE id = ? AND email IS NULL", (email, row[0]))
        return row[0]
    cur.execute("INSERT INTO author (name, email) VALUES (?, ?)", (name, email))
    return cur.lastrowid


def link_author_affiliation(cur, author_id, affiliation_id):
    cur.execute(
        "SELECT 1 FROM author_affiliation WHERE author_id = ? AND affiliation_id = ?",
        (author_id, affiliation_id),
    )
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO author_affiliation (author_id, affiliation_id) VALUES (?, ?)",
            (author_id, affiliation_id),
        )


def get_or_create_algorithm(cur, name, repository=None, documentation=None, website=None):
    cur.execute("SELECT id FROM algorithm WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO algorithm (name, repository, documentation, website) VALUES (?, ?, ?, ?)",
        (name, repository, documentation, website),
    )
    return cur.lastrowid


def create_publication(cur, title, pub_date, doi, publisher, abstract, url, journal, pub_type):
    if doi:
        cur.execute("SELECT id FROM publication WHERE doi = ?", (doi,))
        row = cur.fetchone()
        if row:
            return row[0]
    cur.execute(
        """INSERT INTO publication
           (title, publication_date, doi, publisher, abstract, url, journal, publication_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, pub_date, doi, publisher, abstract, url, journal, pub_type),
    )
    return cur.lastrowid


def link_pub_author(cur, pub_id, author_id, order):
    cur.execute(
        "SELECT 1 FROM publication_author WHERE publication_id = ? AND author_id = ?",
        (pub_id, author_id),
    )
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO publication_author (publication_id, author_id, author_order) VALUES (?, ?, ?)",
            (pub_id, author_id, order),
        )


def link_pub_algorithm(cur, pub_id, alg_id):
    cur.execute(
        "SELECT 1 FROM publication_algorithm WHERE publication_id = ? AND algorithm_id = ?",
        (pub_id, alg_id),
    )
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO publication_algorithm (publication_id, algorithm_id) VALUES (?, ?)",
            (pub_id, alg_id),
        )


# ---------------------------------------------------------------------------
# Affiliation registry: (institution, department, city, country)
# We build these lazily via helper.
# ---------------------------------------------------------------------------

class AffiliationRegistry:
    """Cache country/city/affiliation ids."""

    def __init__(self, cur):
        self.cur = cur
        self._country = {}
        self._city = {}
        self._aff = {}

    def aff(self, institution, department, city, country):
        key = (institution, department, city, country)
        if key not in self._aff:
            cid = self._country.get(country)
            if cid is None:
                cid = get_or_create_country(self.cur, country)
                self._country[country] = cid
            city_key = (city, country)
            cyid = self._city.get(city_key)
            if cyid is None:
                cyid = get_or_create_city(self.cur, city, cid)
                self._city[city_key] = cyid
            self._aff[key] = get_or_create_affiliation(self.cur, institution, department, cyid, cid)
        return self._aff[key]


def populate(conn):
    cur = conn.cursor()
    reg = AffiliationRegistry(cur)

    # ---- helper shortcuts ----
    A = reg.aff  # A(institution, department, city, country) -> affiliation_id

    # Common affiliations used across many papers
    SHANGHAI_AI_LAB = lambda: A("Shanghai Artificial Intelligence Laboratory", None, "Shanghai", "China")
    FUDAN_RICS = lambda: A("Fudan University", "Research Institute of Intelligent Complex Systems", "Shanghai", "China")
    UBC_CS = lambda: A("University of British Columbia", "Department of Computer Science", "Vancouver", "Canada")
    ZHEJIANG_CS = lambda: A("Zhejiang University", "College of Computer Science and Technology", "Hangzhou", "China")
    BEIJING_PROTEOME = lambda: A("Beijing Institute of Lifeomics", "State Key Laboratory of Medical Proteomics, Beijing Proteome Research Center, National Center for Protein Sciences (Beijing)", "Beijing", "China")
    UW_ALLEN = lambda: A("University of Washington", "Paul G. Allen School of Computer Science and Engineering", "Seattle", "USA")
    UW_GENOME = lambda: A("University of Washington", "Department of Genome Sciences", "Seattle", "USA")
    WATERLOO_CS = lambda: A("University of Waterloo", "David R. Cheriton School of Computer Science", "Waterloo", "Canada")
    WATERLOO_STAT = lambda: A("University of Waterloo", "Department of Statistics and Actuarial Science", "Waterloo", "Canada")
    BSI = lambda: A("Bioinformatics Solutions Inc.", None, "Waterloo", "Canada")
    WESTLAKE_MED = lambda: A("Westlake University", "School of Medicine", "Hangzhou", "China")
    WESTLAKE_LIFE = lambda: A("Westlake University", "School of Life Sciences", "Hangzhou", "China")
    WESTLAKE_ENG = lambda: A("Westlake University", "School of Engineering", "Hangzhou", "China")
    TSINGHUA_EE = lambda: A("Tsinghua University", "Department of Electronic Engineering", "Beijing", "China")
    TSINGHUA_LIFE = lambda: A("Tsinghua University", "School of Life Sciences", "Beijing", "China")
    TUM_COMP = lambda: A("Technical University of Munich", "TUM School of Computation, Information and Technology", "Garching", "Germany")
    TUM_LIFE = lambda: A("Technical University of Munich", "TUM School of Life Sciences", "Munich", "Germany")
    TUM_MDSI = lambda: A("Technical University of Munich", "Munich Data Science Institute", "Garching", "Germany")
    PENG_CHENG = lambda: A("Peng Cheng Laboratory", None, "Shenzhen", "China")
    SDUT_CS = lambda: A("Shandong University of Technology", "School of Computer Science and Technology", "Zibo", "China")
    UNT_CSE = lambda: A("University of North Texas", "Department of Computer Science and Engineering", "Denton", "USA")
    UCAS = lambda: A("University of Chinese Academy of Sciences", None, "Beijing", "China")
    ICT_CAS = lambda: A("Chinese Academy of Sciences", "Key Laboratory of Intelligent Information Processing, Institute of Computing Technology", "Beijing", "China")

    # ===================================================================
    # PAPER 1: DiffuNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DiffuNovo")
    pub = create_publication(
        cur, "Regressor-guided Diffusion Model for De Novo Peptide Sequencing with Explicit Mass Control",
        "2026-02-23", None, "AAAI",
        None, None, "AAAI 2026", "peer-reviewed",
    )
    zju = A("Zhejiang University", None, "Hangzhou", "China")
    westlake_ai = A("Westlake University", "AI Lab", "Hangzhou", "China")
    hkust_gz = A("The Hong Kong University of Science and Technology (Guangzhou)", "AIMS Lab", "Guangzhou", "China")
    hkust = A("The Hong Kong University of Science and Technology", None, "Hong Kong", "China")

    a1 = get_or_create_author(cur, "Shaorong Chen", "chenshaorong@westlake.edu.cn")
    link_author_affiliation(cur, a1, zju)
    link_author_affiliation(cur, a1, westlake_ai)

    a2 = get_or_create_author(cur, "Jingbo Zhou")
    link_author_affiliation(cur, a2, zju)
    link_author_affiliation(cur, a2, westlake_ai)

    a3 = get_or_create_author(cur, "Jun Xia", "junxia@hkust-gz.edu.cn")
    link_author_affiliation(cur, a3, hkust_gz)
    link_author_affiliation(cur, a3, hkust)

    for i, aid in enumerate([a1, a2, a3], 1):
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 2: OmniNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "OmniNovo")
    pub = create_publication(
        cur, "Accurate de novo sequencing of the modified proteome with OmniNovo",
        "2025-12-13", "10.48550/arXiv.2512.12272", None,
        None, "https://arxiv.org/abs/2512.12272", "arXiv", "preprint",
    )
    tongji = A("Tongji University", "Shanghai Research Institute for Intelligent Autonomous Systems", "Shanghai", "China")
    westlake_med = WESTLAKE_MED()
    shanghai_innov = A("Shanghai Innovation Institute", None, "Shanghai", "China")
    stony_brook = A("Stony Brook University", "Department of Computer Science", "New York", "USA")

    authors_omni = [
        ("Yuhan Chen", None, [tongji, SHANGHAI_AI_LAB()]),
        ("Shang Qu", None, [SHANGHAI_AI_LAB(), TSINGHUA_EE()]),
        ("Zhiqiang Gao", "gaozhiqiang@pjlab.org.cn", [SHANGHAI_AI_LAB()]),
        ("Yuejin Yang", None, [SHANGHAI_AI_LAB(), FUDAN_RICS()]),
        ("Xiang Zhang", "xzhang23@ualberta.ca", [UBC_CS()]),
        ("Sheng Xu", None, [SHANGHAI_AI_LAB(), FUDAN_RICS()]),
        ("Xinjie Mao", None, [SHANGHAI_AI_LAB(), westlake_med, shanghai_innov]),
        ("Liujia Qian", None, [westlake_med]),
        ("Jiaqi Wei", None, [SHANGHAI_AI_LAB(), ZHEJIANG_CS()]),
        ("Zijie Qiu", None, [SHANGHAI_AI_LAB()]),
        ("Chenyu You", None, [stony_brook]),
        ("Lei Bai", None, [SHANGHAI_AI_LAB()]),
        ("Ning Ding", None, [SHANGHAI_AI_LAB(), TSINGHUA_EE()]),
        ("Tiannan Guo", "guotiannan@westlake.edu.cn", [westlake_med]),
        ("Bowen Zhou", None, [SHANGHAI_AI_LAB(), TSINGHUA_EE()]),
        ("Siqi Sun", "siqisun@fudan.edu.cn", [FUDAN_RICS()]),
    ]
    for i, (name, email, affs) in enumerate(authors_omni, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 3: CrossNovo (Oct 2025, NeurIPS)
    # ===================================================================
    alg = get_or_create_algorithm(cur, "CrossNovo", "https://github.com/BEAM-Labs/denovo")
    pub = create_publication(
        cur, "Bidirectional Representations Augmented Autoregressive Biological Sequence Generation: Applications in De Novo Peptide Sequencing",
        "2025-10-17", "10.48550/arXiv.2510.08169", None,
        None, "https://arxiv.org/abs/2510.08169", "NeurIPS 2025", "peer-reviewed",
    )
    authors_cross = [
        ("Xiang Zhang", None, [FUDAN_RICS(), UBC_CS()]),
        ("Jiaqi Wei", None, [SHANGHAI_AI_LAB(), ZHEJIANG_CS()]),
        ("Zijie Qiu", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
        ("Sheng Xu", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
        ("Zhi Jin", None, [SHANGHAI_AI_LAB()]),
        ("Zhiqiang Gao", None, [SHANGHAI_AI_LAB()]),
        ("Nanqing Dong", None, [SHANGHAI_AI_LAB()]),
        ("Siqi Sun", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
    ]
    for i, (name, email, affs) in enumerate(authors_cross, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 4: Modanovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "Modanovo", "https://github.com/gagneurlab/Modanovo")
    pub = create_publication(
        cur, "Modanovo: A Unified Model for Post-Translational Modification-Aware de Novo Sequencing Using Experimental Spectra from In Vivo and Synthetic Peptides",
        "2025-09-16", "10.1101/2025.09.12.675784", "Cold Spring Harbor Laboratory",
        None, "https://www.biorxiv.org/content/10.1101/2025.09.12.675784v1", "bioRxiv", "preprint",
    )
    tum_virol = A("Technical University of Munich", "Institute of Virology, School of Medicine", "Munich", "Germany")
    nic_slovenia = A("National Institute of Chemistry", None, "Ljubljana", "Slovenia")
    dzif = A("German Centre for Infection Research (DZIF)", None, "Munich", "Germany")
    tum_hg = A("Technical University of Munich", "Institute of Human Genetics, School of Medicine", "Munich", "Germany")
    helmholtz = A("Helmholtz Center Munich", "Computational Health Center", "Neuherberg", "Germany")

    authors_moda = [
        ("Daniela Klaproth-Andrade", None, [TUM_COMP()]),
        ("Yanik Bruns", None, [TUM_COMP()]),
        ("Wassim Gabriel", None, [TUM_LIFE()]),
        ("Christian Nix", None, [TUM_COMP()]),
        ("Valter Bergant", None, [tum_virol, nic_slovenia]),
        ("Andreas Pichlmair", None, [tum_virol, dzif]),
        ("Mathias Wilhelm", None, [TUM_LIFE(), TUM_MDSI()]),
        ("Julien Gagneur", None, [TUM_COMP(), TUM_MDSI(), tum_hg, helmholtz]),
    ]
    for i, (name, email, affs) in enumerate(authors_moda, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 5: DIANovo (PhD thesis - Zheng Ma)
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DIANovo")
    pub = create_publication(
        cur, "Advancing Proteomic Analyses with Graph-Based Deep Learning: Protein Inference and DIA De Novo Peptide Sequencing",
        "2025-08-15", None, "University of Waterloo",
        None, None, None, "peer-reviewed",
    )
    aid = get_or_create_author(cur, "Zheng Ma")
    link_author_affiliation(cur, aid, WATERLOO_CS())
    link_pub_author(cur, pub, aid, 1)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 6: Casanovo v5
    # ===================================================================
    alg = get_or_create_algorithm(cur, "Casanovo", "https://github.com/Noble-Lab/casanovo")
    pub = create_publication(
        cur, "Improvements to CasaNovo, a deep learning de novo peptide sequencer",
        "2025-07-26", "10.1101/2025.07.25.666826", "Cold Spring Harbor Laboratory",
        None, "https://www.biorxiv.org/content/10.1101/2025.07.25.666826v1", "bioRxiv", "preprint",
    )
    talus = A("Talus Bioscience", None, "Seattle", "USA")
    uw_ece = A("University of Washington", "Department of Electrical and Computer Engineering", "Seattle", "USA")
    antwerp = A("University of Antwerp", "Department of Computer Science", "Antwerp", "Belgium")

    authors_cas5 = [
        ("Gwenneth Straub", None, [UW_GENOME()]),
        ("Varun Ananth", None, [UW_ALLEN()]),
        ("William E. Fondrie", None, [talus]),
        ("Chris Hsu", None, [UW_GENOME()]),
        ("Daniela Klaproth-Andrade", None, [TUM_COMP()]),
        ("Michael Riffle", None, [UW_GENOME()]),
        ("Justin Sanders", None, [UW_ALLEN()]),
        ("Bo Wen", None, [UW_GENOME()]),
        ("Lingwen Xu", None, [uw_ece]),
        ("Melih Yilmaz", None, [UW_ALLEN()]),
        ("Michael J. MacCoss", None, [UW_GENOME()]),
        ("Sewoong Oh", None, [UW_ALLEN()]),
        ("Wout Bittremieux", "wout.bittremieux@uantwerpen.be", [antwerp]),
        ("William Stafford Noble", "william-noble@uw.edu", [UW_GENOME(), UW_ALLEN()]),
    ]
    for i, (name, email, affs) in enumerate(authors_cas5, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 7: DiffNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DiffNovo", "https://github.com/Biocomputing-Research-Group/DiffNovo")
    pub = create_publication(
        cur, "DiffNovo: A Transformer-Diffusion Model for De Novo Peptide Sequencing",
        "2025-07-24", "10.1007/978-3-031-94039-2_8", "Springer",
        None, None, "Bioinformatics and Computational Biology (BICOB 2025)", "peer-reviewed",
    )
    authors_diffnovo = [
        ("Shiva Ebrahimi", "Shiva.Ebrahimi@unt.edu", [UNT_CSE()]),
        ("Jiancheng Li", None, [UNT_CSE()]),
        ("Xuan Guo", "Xuan.Guo@unt.edu", [UNT_CSE()]),
    ]
    for i, (name, email, affs) in enumerate(authors_diffnovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 8: RNovA
    # ===================================================================
    alg = get_or_create_algorithm(cur, "RNovA", "https://github.com/AmadeusloveIris/RNovA_SeqFiller_Inference")
    pub = create_publication(
        cur, "Zero-Shot De Novo Peptide Sequencing with Open Post-Translational Modification Discovery",
        "2025-06-27", "10.21203/rs.3.rs-6950964/v1", "Research Square",
        None, None, "Research Square", "preprint",
    )
    baizhen = A("Baizhen Biotechnologies Inc.", None, "Wuhan", "China")
    tsinghua = A("Tsinghua University", None, "Beijing", "China")

    authors_rnova = [
        ("Zeping Mao", None, [WATERLOO_CS()]),
        ("Chao Peng", None, [baizhen]),
        ("Yuling Chen", None, [tsinghua]),
        ("Ping Wu", None, [baizhen]),
        ("Qianqiu Zhang", None, [WATERLOO_CS()]),
        ("Lei Xin", None, [BSI()]),
        ("Haiteng Deng", None, [tsinghua]),
        ("Ming Li", "mli@uwaterloo.ca", [WATERLOO_CS()]),
    ]
    for i, (name, email, affs) in enumerate(authors_rnova, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 9: XuanjiNovo / MassNet
    # ===================================================================
    alg = get_or_create_algorithm(cur, "XuanjiNovo", "https://github.com/guomics-lab/MassNet-DDA")
    pub = create_publication(
        cur, "MassNet: billion-scale AI-friendly mass spectral corpus enables robust de novo peptide sequencing",
        "2025-06-26", "10.1101/2025.06.20.660691", "Cold Spring Harbor Laboratory",
        None, "https://www.biorxiv.org/content/10.1101/2025.06.20.660691v1", "bioRxiv", "preprint",
    )
    westlake_hosp = A("Westlake University", "Affiliated Hangzhou First People's Hospital, School of Medicine", "Hangzhou", "China")
    westlake_proteomics = A("Westlake University", "Westlake Center for Intelligent Proteomics, Westlake Laboratory of Life Sciences and Biomedicine", "Hangzhou", "China")
    westlake_future = A("Westlake University", "Research Center for Industries of the Future, School of Life Sciences", "Hangzhou", "China")
    westlake_omics = A("Westlake Omics (Hangzhou) Biotechnology Co., Ltd.", None, "Hangzhou", "China")

    authors_massnet = [
        ("Jun A", None, [westlake_hosp, westlake_proteomics, westlake_future]),
        ("Xiang Zhang", None, [FUDAN_RICS(), UBC_CS()]),
        ("Xiaofan Zhang", None, [westlake_hosp, westlake_proteomics, westlake_future]),
        ("Jiaqi Wei", None, [SHANGHAI_AI_LAB(), ZHEJIANG_CS()]),
        ("Te Zhang", None, [westlake_hosp, westlake_proteomics, westlake_future]),
        ("Yamin Deng", None, [westlake_hosp, westlake_proteomics, westlake_future]),
        ("Pu Liu", None, [westlake_omics]),
        ("Zongxiang Nie", None, [westlake_proteomics]),
        ("Yi Chen", None, [westlake_proteomics]),
        ("Nanqing Dong", None, [SHANGHAI_AI_LAB()]),
        ("Zhiqiang Gao", None, [SHANGHAI_AI_LAB()]),
        ("Siqi Sun", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
        ("Tiannan Guo", None, [westlake_hosp, westlake_proteomics, westlake_future]),
    ]
    for i, (name, email, affs) in enumerate(authors_massnet, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 10: RefineNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "RefineNovo", "https://github.com/BEAM-Labs/denovo/tree/main/RefineNovo")
    pub = create_publication(
        cur, "Curriculum Learning for Biological Sequence Prediction: The Case of De Novo Peptide Sequencing",
        "2025-06-16", "10.48550/arXiv.2506.13485", None,
        None, "https://arxiv.org/abs/2506.13485", "ICML 2025", "peer-reviewed",
    )
    authors_refine = [
        ("Xiang Zhang", None, [FUDAN_RICS(), UBC_CS()]),
        ("Jiaqi Wei", None, [SHANGHAI_AI_LAB(), ZHEJIANG_CS()]),
        ("Zijie Qiu", None, [SHANGHAI_AI_LAB(), FUDAN_RICS()]),
        ("Sheng Xu", None, [SHANGHAI_AI_LAB(), FUDAN_RICS()]),
        ("Nanqing Dong", None, [SHANGHAI_AI_LAB()]),
        ("Zhiqiang Gao", None, [SHANGHAI_AI_LAB()]),
        ("Siqi Sun", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
    ]
    for i, (name, email, affs) in enumerate(authors_refine, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 11: LIPNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "LIPNovo", "https://github.com/usr922/LIPNovo")
    pub = create_publication(
        cur, "Latent Imputation before Prediction: A New Computational Paradigm for De Novo Peptide Sequencing",
        "2025-05-23", "10.48550/arXiv.2505.17524", None,
        None, "https://arxiv.org/abs/2505.17524", "ICML 2025", "peer-reviewed",
    )
    polyu_bme = A("The Hong Kong Polytechnic University", "Department of Biomedical Engineering", "Hong Kong", "China")
    polyu_comp = A("The Hong Kong Polytechnic University", "Department of Computing", "Hong Kong", "China")
    polyu_abc = A("The Hong Kong Polytechnic University", "Department of Applied Biology and Chemical Technology", "Hong Kong", "China")
    polyu_smart = A("The Hong Kong Polytechnic University", "Research Institute for Smart Ageing", "Hong Kong", "China")
    polyu_aiot = A("The Hong Kong Polytechnic University", "Research Institute for Artificial Intelligence of Things", "Hong Kong", "China")

    authors_lip = [
        ("Ye Du", None, [polyu_bme]),
        ("Chen Yang", None, [polyu_bme]),
        ("Nanxi Yu", None, [polyu_bme]),
        ("Wanyu Lin", None, [polyu_comp]),
        ("Qian Zhao", None, [polyu_abc]),
        ("Shujun Wang", "shu-jun.wang@polyu.edu.hk", [polyu_bme, polyu_smart, polyu_aiot]),
    ]
    for i, (name, email, affs) in enumerate(authors_lip, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 12: TSARseqNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "TSARseqNovo", "https://github.com/qiyueliuhuo8/TSARseqNovo")
    pub = create_publication(
        cur, "A transformer-based semi-autoregressive framework for high-speed and accurate de novo peptide sequencing",
        "2025-02-14", "10.1038/s42003-025-07584-0", None,
        None, "https://doi.org/10.1038/s42003-025-07584-0", "Communications Biology", "peer-reviewed",
    )
    nim_china = A("National Institute of Metrology", None, "Beijing", "China")
    bjut = A("Beijing University of Technology", None, "Beijing", "China")

    authors_tsar = [
        ("Yang Zhao", None, [nim_china, bjut]),
        ("Shuo Wang", None, [nim_china]),
        ("Jinze Huang", None, [nim_china, bjut]),
        ("Bo Meng", None, [bjut]),
        ("Dong An", None, [nim_china]),
        ("Xiang Fang", None, [bjut]),
        ("Yaoguang Wei", None, [nim_china]),
        ("Xinhua Dai", None, [bjut]),
    ]
    for i, (name, email, affs) in enumerate(authors_tsar, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 13: XA-Novo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "XA-Novo", "https://github.com/biocc/SP-MEGD_Fusion")
    pub = create_publication(
        cur, "XA-Novo: an accurate and high-throughput mass spectrometry-based de novo sequencing technology for monoclonal antibodies and antibody mixtures",
        "2025-02-07", "10.21203/rs.3.rs-5858891/v1", "Research Square",
        None, None, "Research Square", "preprint",
    )
    xiangan = A("Xiang An Biomedicine Laboratory", None, "Xiamen", "China")
    xiamen_u = A("Xiamen University", None, "Xiamen", "China")
    fudan_path = A("Fudan University", "Department of Pathology, Zhongshan Hospital", "Shanghai", "China")

    authors_xa = [
        ("Yueting Xiong", None, [xiangan]),
        ("Wenbin Jiang", None, [xiamen_u]),
        ("Jin Xiao", None, [xiamen_u]),
        ("Qingfang Bu", None, [xiamen_u]),
        ("Jingyi Wang", None, [xiangan]),
        ("Zhenjian Jiang", None, [fudan_path]),
        ("Ling Luo", None, [xiamen_u]),
        ("Xiaoqing Chen", None, [xiamen_u]),
        ("Yijie Qiu", None, [xiangan]),
        ("Yangtao Wu", None, [xiamen_u]),
        ("Fan Liu", None, [xiangan]),
        ("Rongshan Yu", None, [xiamen_u]),
        ("Ning-Shao Xia", None, [xiamen_u]),
        ("Quan Yuan", None, [xiamen_u]),
    ]
    for i, (name, email, affs) in enumerate(authors_xa, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 14: CrossNovo (Feb 2025 - ICLR submission)
    # ===================================================================
    pub = create_publication(
        cur, "Distilling Non-Autoregressive Model Knowledge for Autoregressive De Novo Peptide Sequencing",
        "2025-02-05", None, "OpenReview",
        None, None, "ICLR 2025", "peer-reviewed",
    )
    authors_cross2 = [
        ("Xiang Zhang", None, [FUDAN_RICS(), UBC_CS()]),
        ("Jiaqi Wei", None, [SHANGHAI_AI_LAB(), ZHEJIANG_CS()]),
        ("Zijie Qiu", None, [SHANGHAI_AI_LAB(), FUDAN_RICS()]),
        ("Sheng Xu", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
        ("Zhi Jin", None, [SHANGHAI_AI_LAB()]),
        ("Zhiqiang Gao", None, [SHANGHAI_AI_LAB()]),
        ("Nanqing Dong", None, [SHANGHAI_AI_LAB()]),
        ("Siqi Sun", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
    ]
    for i, (name, email, affs) in enumerate(authors_cross2, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)  # same CrossNovo algorithm

    # ===================================================================
    # PAPER 15: π-PrimeNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "π-PrimeNovo", "https://github.com/BEAM-Labs/denovo/tree/main/PrimeNovo")
    pub = create_publication(
        cur, "π-PrimeNovo: an accurate and efficient non-autoregressive deep learning model for de novo peptide sequencing",
        "2025-01-02", "10.1038/s41467-024-55021-3", None,
        None, "https://doi.org/10.1038/s41467-024-55021-3", "Nature Communications", "peer-reviewed",
    )
    soochow = A("Soochow University", None, "Suzhou", "China")
    mbzuai = A("Mohamed bin Zayed University of Artificial Intelligence", None, "Abu Dhabi", "UAE")

    authors_prime = [
        ("Xiang Zhang", None, [SHANGHAI_AI_LAB(), UBC_CS()]),
        ("Tianze Ling", None, [TSINGHUA_LIFE(), BEIJING_PROTEOME()]),
        ("Zhi Jin", None, [SHANGHAI_AI_LAB(), soochow]),
        ("Sheng Xu", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
        ("Zhiqiang Gao", None, [SHANGHAI_AI_LAB()]),
        ("Boyan Sun", None, [BEIJING_PROTEOME()]),
        ("Zijie Qiu", None, [SHANGHAI_AI_LAB(), FUDAN_RICS()]),
        ("Nanqing Dong", None, [SHANGHAI_AI_LAB()]),
        ("Guangshuai Wang", None, [SHANGHAI_AI_LAB()]),
        ("Guibin Wang", None, [BEIJING_PROTEOME()]),
        ("Leyuan Li", None, [BEIJING_PROTEOME()]),
        ("Muhammad Abdul-Mageed", None, [UBC_CS(), mbzuai]),
        ("Laks V.S. Lakshmanan", None, [UBC_CS()]),
        ("Wanli Ouyang", "ouyangwanli@pjlab.org.cn", [SHANGHAI_AI_LAB()]),
        ("Cheng Chang", "changcheng@ncpsb.org.cn", [BEIJING_PROTEOME()]),
        ("Siqi Sun", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
    ]
    for i, (name, email, affs) in enumerate(authors_prime, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 16: ReNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "ReNovo")
    pub = create_publication(
        cur, "ReNovo: Retrieval-Based De Novo Mass Spectrometry Peptide Sequencing",
        "2024-11-27", None, "OpenReview",
        None, None, "ICLR 2025", "peer-reviewed",
    )
    authors_renovo = [
        ("Shaorong Chen", None, [zju, westlake_ai]),
        ("Jun Xia", None, [hkust_gz, hkust]),
        ("Jingbo Zhou", None, [zju, westlake_ai]),
        ("Lecheng Zhang", None, [WESTLAKE_ENG()]),
        ("Zhangyang Gao", None, [WESTLAKE_ENG()]),
        ("Bozhen Hu", None, [WESTLAKE_ENG()]),
        ("Cheng Tan", None, [WESTLAKE_ENG()]),
        ("Wenjie Du", None, [WESTLAKE_ENG()]),
        ("Stan Z. Li", "stan.zq.li@westlake.edu.cn", [WESTLAKE_ENG()]),
    ]
    for i, (name, email, affs) in enumerate(authors_renovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 17: DiaNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DiaNovo", "https://github.com/hearthewind/dianovo")
    pub = create_publication(
        cur, "Disentangling the Complex Multiplexed DIA Spectra in De Novo Peptide Sequencing",
        "2024-11-24", "10.48550/arXiv.2411.15684", None,
        None, "https://arxiv.org/abs/2411.15684", "arXiv", "preprint",
    )
    authors_dianovo = [
        ("Zheng Ma", None, [WATERLOO_CS()]),
        ("Zeping Mao", None, [WATERLOO_CS()]),
        ("Ruixue Zhang", None, [WATERLOO_CS()]),
        ("Jiazhen Chen", None, [WATERLOO_STAT()]),
        ("Lei Xin", None, [BSI()]),
        ("Paul Shan", None, [BSI()]),
        ("Ali Ghodsi", "ali.ghodsi@uwaterloo.ca", [WATERLOO_STAT()]),
        ("Ming Li", None, [WATERLOO_CS()]),
    ]
    for i, (name, email, affs) in enumerate(authors_dianovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 18: RankNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "RankNovo")
    pub = create_publication(
        cur, "RankNovo: A Universal Reranking Approach for Robust De Novo Peptide Sequencing",
        "2024-11-20", None, "OpenReview",
        None, None, "ICLR 2025", "peer-reviewed",
    )
    authors_ranknovo = [
        ("Zijie Qiu", None, [SHANGHAI_AI_LAB(), FUDAN_RICS()]),
        ("Jiaqi Wei", None, [SHANGHAI_AI_LAB(), ZHEJIANG_CS()]),
        ("Xiang Zhang", None, [FUDAN_RICS(), UBC_CS()]),
        ("Sheng Xu", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
        ("Kai Zou", None, [SHANGHAI_AI_LAB()]),
        ("Zhi Jin", None, [SHANGHAI_AI_LAB()]),
        ("Zhiqiang Gao", None, [SHANGHAI_AI_LAB()]),
        ("Nanqing Dong", None, [SHANGHAI_AI_LAB()]),
        ("Siqi Sun", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
    ]
    for i, (name, email, affs) in enumerate(authors_ranknovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 19: π-xNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "π-xNovo", "https://github.com/PHOENIXcenter/pi-xNovo")
    pub = create_publication(
        cur, "Transforming de novo peptide sequencing by explainable AI",
        "2024-08-04", "10.21203/rs.3.rs-4716013/v1", "Research Square",
        None, None, "Research Square", "preprint",
    )
    tsinghua_sz = A("Tsinghua University", "Tsinghua Shenzhen International Graduate School", "Shenzhen", "China")
    ncpsb = A("Beijing Institute of Lifeomics", "National Center for Protein Sciences (Beijing)", "Beijing", "China")

    authors_pixnovo = [
        ("Yu Wang", "wangy20@pcl.ac.cn", [PENG_CHENG()]),
        ("Zhendong Liang", None, [PENG_CHENG()]),
        ("Tianze Ling", None, [BEIJING_PROTEOME()]),
        ("Cheng Chang", None, [ncpsb]),
        ("Tingpeng Yang", None, [PENG_CHENG(), tsinghua_sz]),
        ("Linhai Xie", None, [BEIJING_PROTEOME()]),
        ("Yonghong He", None, [tsinghua_sz]),
    ]
    for i, (name, email, affs) in enumerate(authors_pixnovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 20: Casanovo V2 (Nature Comms)
    # ===================================================================
    pub = create_publication(
        cur, "Sequence-to-sequence translation from mass spectra to peptides with a transformer model",
        "2024-07-30", "10.1038/s41467-024-49731-x", None,
        None, "https://doi.org/10.1038/s41467-024-49731-x", "Nature Communications", "peer-reviewed",
    )
    authors_cas2 = [
        ("Melih Yilmaz", None, [UW_ALLEN()]),
        ("William E. Fondrie", None, [talus]),
        ("Wout Bittremieux", None, [antwerp]),
        ("Rowan Nelson", None, [UW_GENOME()]),
        ("Varun Ananth", None, [UW_ALLEN()]),
        ("Sewoong Oh", None, [UW_ALLEN()]),
        ("William Stafford Noble", None, [UW_GENOME(), UW_ALLEN()]),
    ]
    for i, (name, email, affs) in enumerate(authors_cas2, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)  # same Casanovo algorithm

    # ===================================================================
    # PAPER 21: TransNovo (no PDF)
    # ===================================================================
    alg = get_or_create_algorithm(cur, "TransNovo", "https://github.com/ThatMatin/TransNovo")
    pub = create_publication(
        cur, "TransNovo",
        "2024-07-15", None, None,
        None, "https://github.com/ThatMatin/TransNovo", None, "preprint",
    )
    aid = get_or_create_author(cur, "Matin Ahmadi")
    link_pub_author(cur, pub, aid, 1)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 22: PowerNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "PowerNovo", "https://github.com/protdb/PowerNovo")
    pub = create_publication(
        cur, "PowerNovo: de novo peptide sequencing via tandem mass spectrometry using an ensemble of transformer and BERT models",
        "2024-07-01", "10.1038/s41598-024-65861-0", None,
        None, "https://doi.org/10.1038/s41598-024-65861-0", "Scientific Reports", "peer-reviewed",
    )
    ibmc = A("Institute of Biomedical Chemistry", None, "Moscow", "Russia")
    authors_powernovo = [
        ("Denis V. Petrovskiy", None, [ibmc]),
        ("Kirill S. Nikolsky", None, [ibmc]),
        ("Liudmila I. Kulikova", None, [ibmc]),
        ("Vladimir R. Rudnev", None, [ibmc]),
        ("Tatiana V. Butkova", None, [ibmc]),
        ("Kristina A. Malsagova", None, [ibmc]),
        ("Arthur T. Kopylov", None, [ibmc]),
        ("Anna L. Kaysheva", "kaysheva1@gmail.com", [ibmc]),
    ]
    for i, (name, email, affs) in enumerate(authors_powernovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 23: Cascadia
    # ===================================================================
    alg = get_or_create_algorithm(cur, "Cascadia")
    pub = create_publication(
        cur, "A transformer model for de novo sequencing of data independent acquisition mass spectrometry data",
        "2024-06-04", "10.1101/2024.06.03.597251", "Cold Spring Harbor Laboratory",
        None, "https://www.biorxiv.org/content/10.1101/2024.06.03.597251v1", "bioRxiv", "preprint",
    )
    spectragen = A("Spectragen Informatics", None, "Seattle", "USA")
    authors_cascadia = [
        ("Justin Sanders", None, [UW_ALLEN()]),
        ("Bo Wen", None, [UW_GENOME()]),
        ("Paul Rudnick", None, [spectragen]),
        ("Rich Johnson", None, [UW_GENOME()]),
        ("Christine C. Wu", None, [UW_GENOME()]),
        ("Sewoong Oh", None, [UW_ALLEN()]),
        ("Michael J. MacCoss", None, [UW_GENOME()]),
        ("William Stafford Noble", None, [UW_GENOME(), UW_ALLEN()]),
    ]
    for i, (name, email, affs) in enumerate(authors_cascadia, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 24: DiaTrans (Casanovo-DIA)
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DiaTrans", "https://github.com/Biocomputing-Research-Group/Casanovo-DIA")
    pub = create_publication(
        cur, "Transformer-Based De Novo Peptide Sequencing for Data-Independent Acquisition Mass Spectrometry",
        "2024-04-09", "10.48550/arXiv.2305.14920", None,
        None, "https://arxiv.org/abs/2305.14920", "arXiv", "preprint",
    )
    authors_diatrans = [
        ("Shiva Ebrahimi", None, [UNT_CSE()]),
        ("Xuan Guo", None, [UNT_CSE()]),
    ]
    for i, (name, email, affs) in enumerate(authors_diatrans, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 25: pXg
    # ===================================================================
    alg = get_or_create_algorithm(cur, "pXg", "https://github.com/progistar/pXg")
    pub = create_publication(
        cur, "pXg: Comprehensive Identification of Noncanonical MHC-I-Associated Peptides From De Novo Peptide Sequencing Using RNA-Seq Reads",
        "2024-04-01", "10.1016/j.mcpro.2024.100743", None,
        None, "https://doi.org/10.1016/j.mcpro.2024.100743", "Molecular and Cellular Proteomics", "peer-reviewed",
    )
    hanyang = A("Hanyang University", None, "Seoul", "South Korea")
    authors_pxg = [
        ("Seunghyuk Choi", None, [hanyang]),
        ("Eunok Paek", "eunokpaek@hanyang.ac.kr", [hanyang]),
    ]
    for i, (name, email, affs) in enumerate(authors_pxg, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 26: AdaNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "AdaNovo")
    pub = create_publication(
        cur, "AdaNovo: Adaptive De Novo Peptide Sequencing with Conditional Mutual Information",
        "2024-03-15", "10.48550/arXiv.2403.07013", None,
        None, "https://arxiv.org/abs/2403.07013", "ICML 2024", "peer-reviewed",
    )
    usc = A("University of Southern California", None, "Los Angeles", "USA")
    authors_adanovo = [
        ("Jun Xia", None, [WESTLAKE_ENG()]),
        ("Shaorong Chen", None, [WESTLAKE_ENG()]),
        ("Jingbo Zhou", None, [WESTLAKE_ENG()]),
        ("Tianze Ling", None, [tsinghua]),
        ("Wenjie Du", None, [WESTLAKE_ENG()]),
        ("Sizhe Liu", None, [usc]),
        ("Stan Z. Li", None, [WESTLAKE_ENG()]),
    ]
    for i, (name, email, affs) in enumerate(authors_adanovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 27: NovoB
    # ===================================================================
    alg = get_or_create_algorithm(cur, "NovoB", "https://github.com/ProteomeTeam/NovoB")
    pub = create_publication(
        cur, "Bidirectional de novo peptide sequencing using a transformer model",
        "2024-02-28", "10.1371/journal.pcbi.1011892", None,
        None, "https://doi.org/10.1371/journal.pcbi.1011892", "PLoS Computational Biology", "peer-reviewed",
    )
    kisti = A("Korea Institute of Science and Technology Information", "Center for Biomedical Computing", "Daejeon", "South Korea")
    authors_novob = [
        ("Sangjeong Lee", None, [kisti]),
        ("Hyunwoo Kim", "pardess@kisti.re.kr", [kisti]),
    ]
    for i, (name, email, affs) in enumerate(authors_novob, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 28: π-HelixNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "π-HelixNovo", "https://github.com/PHOENIXcenter/pi-HelixNovo")
    pub = create_publication(
        cur, "Introducing π-HelixNovo for practical large-scale de novo peptide sequencing",
        "2024-02-10", "10.1093/bib/bbae019", None,
        None, "https://doi.org/10.1093/bib/bbae019", "Briefings in Bioinformatics", "peer-reviewed",
    )
    proteomics_unit = A("Beijing Institute of Lifeomics", "Research Unit of Proteomics Driven Cancer Precision Medicine, Chinese Academy of Medical Sciences", "Beijing", "China")

    authors_helix = [
        ("Tingpeng Yang", None, [PENG_CHENG(), tsinghua_sz]),
        ("Tianze Ling", None, [BEIJING_PROTEOME(), TSINGHUA_LIFE()]),
        ("Boyan Sun", None, [BEIJING_PROTEOME()]),
        ("Zhendong Liang", None, [PENG_CHENG(), tsinghua_sz]),
        ("Fan Xu", None, [PENG_CHENG()]),
        ("Xiansong Huang", None, [PENG_CHENG()]),
        ("Linhai Xie", None, [BEIJING_PROTEOME()]),
        ("Yonghong He", None, [PENG_CHENG(), tsinghua_sz]),
        ("Leyuan Li", None, [BEIJING_PROTEOME()]),
        ("Fuchu He", None, [BEIJING_PROTEOME(), proteomics_unit]),
        ("Yu Wang", None, [PENG_CHENG()]),
        ("Cheng Chang", None, [BEIJING_PROTEOME(), proteomics_unit]),
    ]
    for i, (name, email, affs) in enumerate(authors_helix, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 29: Casanovo-DB
    # ===================================================================
    alg = get_or_create_algorithm(cur, "Casanovo-DB")
    pub = create_publication(
        cur, "A learned score function improves the power of mass spectrometry database search",
        "2024-02-07", "10.1101/2024.01.26.577425", "Cold Spring Harbor Laboratory",
        None, "https://www.biorxiv.org/content/10.1101/2024.01.26.577425v1", "bioRxiv", "preprint",
    )
    authors_casdb = [
        ("Varun Ananth", None, [UW_ALLEN()]),
        ("Justin Sanders", None, [UW_ALLEN()]),
        ("Melih Yilmaz", None, [UW_ALLEN()]),
        ("Sewoong Oh", None, [UW_ALLEN()]),
        ("William Stafford Noble", None, [UW_GENOME(), UW_ALLEN()]),
    ]
    for i, (name, email, affs) in enumerate(authors_casdb, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 30: MARS
    # ===================================================================
    alg = get_or_create_algorithm(cur, "MARS")
    pub = create_publication(
        cur, "MARS an improved de novo peptide candidate selection method for non-canonical antigen target discovery in cancer",
        "2024-01-22", "10.1038/s41467-023-44460-z", None,
        None, "https://doi.org/10.1038/s41467-023-44460-z", "Nature Communications", "peer-reviewed",
    )
    jenner = A("University of Oxford", "The Jenner Institute", "Oxford", "UK")
    oxford_immuno = A("University of Oxford", "Centre for Immuno-Oncology, Nuffield Department of Medicine", "Oxford", "UK")
    dtu_plain = A("Technical University of Denmark", None, "Copenhagen", "Denmark")
    paris_cochin = A("Université Paris Cité", "Institut Cochin, CNRS, INSERM", "Paris", "France")
    chop = A("Children's Hospital of Philadelphia", "Department of Pathology and Laboratory Medicine", "Philadelphia", "USA")
    upenn = A("University of Pennsylvania", "Department of Pathology and Laboratory Medicine, Perelman School of Medicine", "Philadelphia", "USA")
    ap_hp = A("Assistance Publique Hôpitaux de Paris", "Service de Diabétologie et Immunologie Clinique, Cochin Hospital", "Paris", "France")
    utrecht = A("Utrecht University", "Department of Pharmaceutical Sciences", "Utrecht", "Netherlands")

    authors_mars = [
        ("Hanqing Liao", None, [jenner, oxford_immuno]),
        ("Carolina Barra", None, [dtu_plain]),
        ("Zhicheng Zhou", None, [paris_cochin]),
        ("Xu Peng", None, [jenner]),
        ("Isaac Woodhouse", None, [jenner, oxford_immuno]),
        ("Arun Tailor", None, [jenner, oxford_immuno]),
        ("Robert Parker", None, [jenner, oxford_immuno]),
        ("Alexia Carré", None, [paris_cochin]),
        ("Persephone Borrow", None, [oxford_immuno]),
        ("Michael J. Hogan", None, [chop]),
        ("Wayne Paes", None, [jenner, oxford_immuno]),
        ("Laurence C. Eisenlohr", None, [chop, upenn]),
        ("Roberto Mallone", None, [paris_cochin, ap_hp]),
        ("Morten Nielsen", None, [dtu_plain]),
        ("Nicola Ternette", "nicola.ternette@ndm.ox.ac.uk", [jenner, oxford_immuno, utrecht]),
    ]
    for i, (name, email, affs) in enumerate(authors_mars, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 31: Spectralis
    # ===================================================================
    alg = get_or_create_algorithm(cur, "Spectralis", "https://github.com/gagneurlab/spectralis")
    pub = create_publication(
        cur, "Deep learning-driven fragment ion series classification enables highly precise and sensitive de novo peptide sequencing",
        "2024-01-02", "10.1038/s41467-023-44323-7", None,
        None, "https://doi.org/10.1038/s41467-023-44323-7", "Nature Communications", "peer-reviewed",
    )
    tum_cmm = A("Technical University of Munich", "Computational Molecular Medicine, School of Computation, Information and Technology", "Munich", "Germany")
    tum_cms = A("Technical University of Munich", "Computational Mass Spectrometry, School of Life Sciences", "Munich", "Germany")

    authors_spectralis = [
        ("Daniela Klaproth-Andrade", None, [tum_cmm]),
        ("Johannes Hingerl", None, [tum_cmm]),
        ("Nicholas H. Smith", None, [tum_cmm]),
        ("Jakob Träuble", None, [tum_cmm]),
        ("Mathias Wilhelm", None, [tum_cms]),
        ("Julien Gagneur", None, [tum_cmm, tum_hg, helmholtz, TUM_MDSI()]),
    ]
    for i, (name, email, affs) in enumerate(authors_spectralis, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 32: ContraNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "ContraNovo", "https://github.com/BEAM-Labs/ContraNovo")
    pub = create_publication(
        cur, "ContraNovo: A Contrastive Learning Approach to Enhance De Novo Peptide Sequencing",
        "2023-12-18", None, "AAAI",
        None, None, "AAAI 2024", "peer-reviewed",
    )
    authors_contra = [
        ("Zhi Jin", None, [SHANGHAI_AI_LAB(), soochow]),
        ("Sheng Xu", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
        ("Xiang Zhang", None, [SHANGHAI_AI_LAB(), UBC_CS()]),
        ("Tianze Ling", None, [ncpsb]),
        ("Nanqing Dong", None, [SHANGHAI_AI_LAB()]),
        ("Wanli Ouyang", None, [SHANGHAI_AI_LAB()]),
        ("Zhiqiang Gao", None, [SHANGHAI_AI_LAB()]),
        ("Cheng Chang", None, [ncpsb]),
        ("Siqi Sun", None, [FUDAN_RICS(), SHANGHAI_AI_LAB()]),
    ]
    for i, (name, email, affs) in enumerate(authors_contra, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 33: PepNet
    # ===================================================================
    alg = get_or_create_algorithm(cur, "PepNet", "https://github.com/lkytal/PepNet")
    pub = create_publication(
        cur, "Accurate de novo peptide sequencing using fully convolutional neural networks",
        "2023-12-02", "10.1038/s41467-023-43010-x", None,
        None, "https://doi.org/10.1038/s41467-023-43010-x", "Nature Communications", "peer-reviewed",
    )
    iu_luddy = A("Indiana University Bloomington", "Luddy School of Informatics, Computing, and Engineering", "Bloomington", "USA")
    authors_pepnet = [
        ("Kaiyuan Liu", None, [iu_luddy]),
        ("Yuzhen Ye", None, [iu_luddy]),
        ("Sujun Li", None, [iu_luddy]),
        ("Haixu Tang", None, [iu_luddy]),
    ]
    for i, (name, email, affs) in enumerate(authors_pepnet, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 34: DeepNovo Peptidome (DeepImmu, DeepSelf)
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DeepNovo Peptidome")
    pub = create_publication(
        cur, "A complete mass spectrometry-based immunopeptidomics pipeline for neoantigen identification and validation",
        "2023-11-09", "10.21203/rs.3.rs-3548498/v1", "Research Square",
        None, None, "Research Square", "preprint",
    )
    authors_deepimmun = [
        ("Ming Li", None, [WATERLOO_CS()]),
        ("Ngoc Hieu Tran", None, [WATERLOO_CS()]),
    ]
    for i, (name, email, affs) in enumerate(authors_deepimmun, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 35: GraphNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "GraphNovo", "https://github.com/AmadeusloveIris/Graphnovo")
    pub = create_publication(
        cur, "Mitigating the missing-fragmentation problem in de novo peptide sequencing with a two-stage graph-based deep learning model",
        "2023-10-19", "10.1038/s42256-023-00738-x", None,
        None, "https://doi.org/10.1038/s42256-023-00738-x", "Nature Machine Intelligence", "peer-reviewed",
    )
    authors_graphnovo = [
        ("Zeping Mao", None, [WATERLOO_CS()]),
        ("Ruixue Zhang", None, [WATERLOO_CS()]),
        ("Lei Xin", None, [BSI()]),
        ("Ming Li", None, [WATERLOO_CS()]),
    ]
    for i, (name, email, affs) in enumerate(authors_graphnovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 36: SeqNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "SeqNovo")
    pub = create_publication(
        cur, "SeqNovo: De Novo Peptide Sequencing Prediction in IoMT via Seq2Seq",
        "2023-10-04", "10.1109/JBHI.2023.3322277", None,
        None, None, "IEEE Journal of Biomedical and Health Informatics", "peer-reviewed",
    )
    guizhou = A("Guizhou University", "Key Laboratory of Advanced Manufacturing Technology", "Guiyang", "China")
    jinan = A("Jinan University", "College of Information Science and Technology", "Guangzhou", "China")

    authors_seqnovo = [
        ("Ke Wang", "wangke@jnu.edu.cn", [guizhou, jinan]),
        ("Mingjia Zhu", None, [jinan]),
    ]
    for i, (name, email, affs) in enumerate(authors_seqnovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 37: GlycanFinder
    # ===================================================================
    alg = get_or_create_algorithm(cur, "GlycanFinder")
    pub = create_publication(
        cur, "Glycopeptide database search and de novo sequencing with PEAKS GlycanFinder enable highly sensitive glycoproteomics",
        "2023-07-08", "10.1038/s41467-023-39699-5", None,
        None, "https://doi.org/10.1038/s41467-023-39699-5", "Nature Communications", "peer-reviewed",
    )
    authors_glycan = [
        ("Weiping Sun", None, [BSI()]),
        ("Qianqiu Zhang", None, [WATERLOO_CS()]),
    ]
    for i, (name, email, affs) in enumerate(authors_glycan, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 38: DpNovo (MSc thesis)
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DpNovo")
    pub = create_publication(
        cur, "DpNovo: A DEEP LEARNING MODEL COMBINED WITH DYNAMIC PROGRAMMING FOR DE NOVO PEPTIDE SEQUENCING",
        "2023-07-07", None, "Western University",
        None, "https://ir.lib.uwo.ca/etd/9373", None, "peer-reviewed",
    )
    western = A("Western University", "Department of Computer Science", "London", "Canada")
    aid = get_or_create_author(cur, "Yizhou Li")
    link_author_affiliation(cur, aid, western)
    link_pub_author(cur, pub, aid, 1)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 39: BiATNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "BiATNovo")
    pub = create_publication(
        cur, "BiATNovo: A Self-Attention based Bidirectional Peptide Sequencing Method",
        "2023-05-14", "10.1101/2023.05.12.540277", "Cold Spring Harbor Laboratory",
        None, "https://www.biorxiv.org/content/10.1101/2023.05.12.540277v1", "bioRxiv", "preprint",
    )
    beihang = A("Beihang University", "Sino-German Joint Software Institute (JSI)", "Beijing", "China")
    westlake_prov = A("Westlake University", "Zhejiang Provincial Laboratory of Life Sciences and Biomedicine, School of Life Sciences", "Hangzhou", "China")
    westlake_inst = A("Westlake Institute for Advanced Study", "Institute of Basic Medical Sciences", "Hangzhou", "China")
    pku_cc = A("Peking University", "Computer Center", "Beijing", "China")

    authors_biat = [
        ("Siyu Wu", None, [beihang]),
        ("Zhongzhi Luan", "luan.zhongzhi@buaa.edu.cn", [beihang]),
        ("Zhenxin Fu", None, [pku_cc]),
        ("Qunying Wang", None, [beihang]),
        ("Tiannan Guo", "guotiannan@westlake.edu.cn", [westlake_prov, westlake_inst]),
    ]
    for i, (name, email, affs) in enumerate(authors_biat, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 40: PGPointNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "PGPointNovo", "https://github.com/shallFun4Learning/PGPointNovo")
    pub = create_publication(
        cur, "PGPointNovo: an efficient neural network-based tool for parallel de novo peptide sequencing",
        "2023-04-25", "10.1093/bioadv/vbad057", None,
        None, "https://doi.org/10.1093/bioadv/vbad057", "Bioinformatics Advances", "peer-reviewed",
    )
    cqupt = A("Chongqing University of Posts and Telecommunications", "School of Computer Science and Technology", "Chongqing", "China")
    swinburne = A("Swinburne University of Technology", "School of Software and Electrical Engineering", "Melbourne", "Australia")
    sysu = A("Sun Yat-Sen University", "School of Computer Science and Engineering", "Guangzhou", "China")
    pla_hospital = A("Chinese PLA General Hospital", "Department of General Surgery, First Medical Center", "Beijing", "China")

    authors_pgpoint = [
        ("Xiaofang Xu", None, [cqupt]),
        ("Chunde Yang", None, [cqupt]),
        ("Qiang He", None, [swinburne]),
        ("Kunxian Shu", None, [cqupt]),
        ("Yuan Xinpu", None, [pla_hospital]),
        ("Zhiguang Chen", "zhiguang.chen@nscc-gz.cn", [sysu]),
        ("Yunping Zhu", "zhuyunping@ncpsb.org.cn", [BEIJING_PROTEOME()]),
        ("Tao Chen", "taochen1019@163.com", [BEIJING_PROTEOME()]),
    ]
    for i, (name, email, affs) in enumerate(authors_pgpoint, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 41: PaSER Novor
    # ===================================================================
    alg = get_or_create_algorithm(cur, "PaSER Novor")
    pub = create_publication(
        cur, "PaSER Novor: Real-time de novo sequencing for 4D-Proteomics applications",
        "2023-04-20", None, None,
        None, None, None, "preprint",
    )
    rapid_novor = A("Rapid Novor Inc.", None, "Kitchener", "Canada")
    bruker_de = A("Bruker Daltonics GmbH & Co. KG", None, "Bremen", "Germany")
    bruker_ca = A("Bruker Ltd.", None, "Milton", "Canada")
    bruker_ch = A("Bruker AG", None, "Fällanden", "Switzerland")

    authors_paser = [
        ("Rui Zhang", None, [rapid_novor]),
        ("Qixin Liu", None, [rapid_novor]),
        ("Mingjie Xie", None, [rapid_novor]),
        ("Dennis Trede", None, [bruker_de]),
        ("Tharan Srikumar", None, [bruker_ca]),
        ("Jonathan Krieger", None, [bruker_ca]),
        ("Bin Ma", None, [rapid_novor]),
        ("George Rosenberger", None, [bruker_ch]),
    ]
    for i, (name, email, affs) in enumerate(authors_paser, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 42: Denovo-GCN
    # ===================================================================
    alg = get_or_create_algorithm(cur, "Denovo-GCN")
    pub = create_publication(
        cur, "Denovo-GCN: De Novo Peptide Sequencing by Graph Convolutional Neural Networks",
        "2023-04-05", "10.3390/app13074604", None,
        None, "https://doi.org/10.3390/app13074604", "Applied Sciences (MDPI)", "peer-reviewed",
    )
    authors_gcn = [
        ("Ruitao Wu", None, [SDUT_CS()]),
        ("Xiang Zhang", None, [SDUT_CS()]),
        ("Runtao Wang", None, [SDUT_CS()]),
        ("Haipeng Wang", None, [SDUT_CS()]),
    ]
    for i, (name, email, affs) in enumerate(authors_gcn, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 43: Casanovo V1 (preprint)
    # ===================================================================
    ucsd = A("University of California San Diego", "Skaggs School of Pharmacy and Pharmaceutical Science", "San Diego", "USA")
    pub = create_publication(
        cur, "De novo mass spectrometry peptide sequencing with a transformer model",
        "2023-02-09", "10.1101/2022.02.07.479481", "Cold Spring Harbor Laboratory",
        None, "https://www.biorxiv.org/content/10.1101/2022.02.07.479481v1", "bioRxiv", "preprint",
    )
    authors_cas1 = [
        ("Melih Yilmaz", None, [UW_ALLEN()]),
        ("William E. Fondrie", None, [talus]),
        ("Wout Bittremieux", None, [ucsd]),
        ("Sewoong Oh", None, [UW_ALLEN()]),
        ("William Stafford Noble", None, [UW_GENOME(), UW_ALLEN()]),
    ]
    for i, (name, email, affs) in enumerate(authors_cas1, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)  # same Casanovo algorithm

    # ===================================================================
    # PAPER 44: NovoRank
    # ===================================================================
    alg = get_or_create_algorithm(cur, "NovoRank", "https://github.com/HanyangBISLab/NovoRank")
    pub = create_publication(
        cur, "NovoRank: Refinement for De Novo Peptide Sequencing Based on Spectral Clustering and Deep Learning",
        "2022-08-01", "10.1021/acs.jproteome.2c00062", None,
        None, "https://doi.org/10.1021/acs.jproteome.2c00062", "Journal of Proteome Research", "peer-reviewed",
    )
    authors_novorank = [
        ("Jangho Seo", None, [hanyang]),
        ("Seunghyuk Choi", None, [hanyang]),
        ("Eunok Paek", None, [hanyang]),
    ]
    for i, (name, email, affs) in enumerate(authors_novorank, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 45: Kaiko
    # ===================================================================
    alg = get_or_create_algorithm(cur, "Kaiko", "https://github.com/PNNL-Comp-Mass-Spec/Kaiko")
    pub = create_publication(
        cur, "Uncovering Hidden Members and Functions of the Soil Microbiome Using De Novo Metaproteomics",
        "2022-07-06", "10.1021/acs.jproteome.2c00334", None,
        None, "https://doi.org/10.1021/acs.jproteome.2c00334", "Journal of Proteome Research", "peer-reviewed",
    )
    pnnl = A("Pacific Northwest National Laboratory", None, "Richland", "USA")
    byu = A("Brigham Young University", None, "Provo", "USA")

    authors_kaiko = [
        ("Joon-Yong Lee", None, [pnnl]),
        ("Hugh D. Mitchell", None, [pnnl]),
    ]
    for i, (name, email, affs) in enumerate(authors_kaiko, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 46: DePS
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DePS")
    pub = create_publication(
        cur, "DePS: An improved deep learning model for de novo peptide sequencing",
        "2022-03-16", "10.48550/arXiv.2203.08820", None,
        None, "https://arxiv.org/abs/2203.08820", "arXiv", "preprint",
    )
    jsut = A("Jiangsu University of Technology", "Institute of Bioinformatics and Medical Engineering, School of Electrical and Information Engineering", "Changzhou", "China")
    czu = A("Changzhou University", "School of Computer Science and Artificial Intelligence & Aliyun School of Big Data", "Changzhou", "China")
    cumt = A("China University of Mining and Technology", "School of Mathematics", "Xuzhou", "China")

    authors_deps = [
        ("Cheng Ge", "13851520957@163.com", [jsut]),
        ("Yi Lu", None, [jsut]),
        ("Jia Qu", None, [czu]),
        ("Liangxu Xie", None, [jsut]),
        ("Feng Wang", None, [czu]),
        ("Hong Zhang", None, [cumt]),
        ("Ren Kong", "rkong@jsut.edu.cn", [jsut]),
        ("Shan Chang", "schang@jsut.edu.cn", [jsut]),
    ]
    for i, (name, email, affs) in enumerate(authors_deps, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 47: Deep Novo A+ (no PDF)
    # ===================================================================
    alg = get_or_create_algorithm(cur, "Deep Novo A+")
    pub = create_publication(
        cur, "Deep Novo A+: Improving the Deep Learning Model for De Novo Peptide Sequencing with Additional Ion Types and Validation Set",
        "2022-02-01", None, None,
        None, None, "Current Bioinformatics", "peer-reviewed",
    )
    authors_dnap = [
        ("Lei Di", None, []),
        ("Yongxing He", None, []),
    ]
    for i, (name, email, affs) in enumerate(authors_dnap, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 48: PointNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "PointNovo", "https://github.com/irleader/PointNovo")
    pub = create_publication(
        cur, "Computationally instrument-resolution-independent de novo peptide sequencing for high-resolution devices",
        "2021-03-18", "10.1038/s42256-021-00304-3", None,
        None, "https://doi.org/10.1038/s42256-021-00304-3", "Nature Machine Intelligence", "peer-reviewed",
    )
    authors_pointnovo = [
        ("Rui Qiao", None, [WATERLOO_STAT()]),
        ("Ngoc Hieu Tran", None, [WATERLOO_CS()]),
    ]
    for i, (name, email, affs) in enumerate(authors_pointnovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 49: DeepNovoAA
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DeepNovoAA", "https://github.com/nh2tran/DeepNovoAA")
    pub = create_publication(
        cur, "Personalized deep learning of individual immunopeptidomes to identify neoantigens for cancer vaccines",
        "2020-11-16", "10.1038/s42256-020-00260-4", None,
        None, "https://doi.org/10.1038/s42256-020-00260-4", "Nature Machine Intelligence", "peer-reviewed",
    )
    authors_denovoaa = [
        ("Ngoc Hieu Tran", None, [WATERLOO_CS()]),
        ("Rui Qiao", None, [WATERLOO_STAT()]),
        ("Lei Xin", None, [BSI()]),
        ("Xin Chen", None, [BSI()]),
        ("Baozhen Shan", "bshan@bioinfor.com", [BSI()]),
        ("Ming Li", None, [WATERLOO_CS()]),
    ]
    for i, (name, email, affs) in enumerate(authors_denovoaa, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 50: CycloNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "CycloNovo", "https://github.com/bbehsaz/cyclonovo")
    pub = create_publication(
        cur, "De Novo Peptide Sequencing Reveals Many Cyclopeptides in the Human Gut and Other Environments",
        "2020-01-22", "10.1016/j.cels.2019.11.007", None,
        None, "https://doi.org/10.1016/j.cels.2019.11.007", "Cell Systems", "peer-reviewed",
    )
    ucsd_plain = A("University of California San Diego", None, "San Diego", "USA")
    cmu = A("Carnegie Mellon University", None, "Pittsburgh", "USA")

    authors_cyclo = [
        ("Bahar Behsaz", "bbehsaz@ucsd.edu", [ucsd_plain]),
        ("Hosein Mohimani", None, [cmu]),
    ]
    for i, (name, email, affs) in enumerate(authors_cyclo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 51: Peptide Sequencing with Deep Learning (PhD thesis)
    # ===================================================================
    pub = create_publication(
        cur, "Peptide Sequencing with Deep Learning",
        "2020-01-01", None, "University of Waterloo",
        None, None, None, "peer-reviewed",
    )
    aid = get_or_create_author(cur, "Rui Qiao", "rqiao@uwaterloo.ca")
    link_author_affiliation(cur, aid, WATERLOO_STAT())
    link_pub_author(cur, pub, aid, 1)

    # ===================================================================
    # PAPER 52: pNovo 3
    # ===================================================================
    alg = get_or_create_algorithm(cur, "pNovo 3", "http://pfind.org/software/pNovo/index.html")
    pub = create_publication(
        cur, "pNovo 3: precise de novo peptide sequencing using a learning-to-rank framework",
        "2019-07-24", "10.1093/bioinformatics/btz366", None,
        None, "https://doi.org/10.1093/bioinformatics/btz366", "Bioinformatics", "peer-reviewed",
    )
    authors_pnovo = [
        ("Hao Yang", None, [ICT_CAS(), UCAS()]),
        ("Hao Chi", "chihao@ict.ac.cn", [ICT_CAS(), UCAS()]),
        ("Wen-Feng Zeng", None, [ICT_CAS(), UCAS()]),
        ("Wen-Jing Zhou", None, [ICT_CAS(), UCAS()]),
        ("Si-Min He", "smhe@ict.ac.cn", [ICT_CAS(), UCAS()]),
    ]
    for i, (name, email, affs) in enumerate(authors_pnovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 53: DeepNovo V2
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DeepNovo V2", "https://github.com/volpato30/DeepNovoV2")
    pub = create_publication(
        cur, "DeepNovoV2: Better de novo peptide sequencing with deep learning",
        "2019-05-22", "10.48550/arXiv.1904.08514", None,
        None, "https://arxiv.org/abs/1904.08514", "arXiv", "preprint",
    )
    authors_dnv2 = [
        ("Rui Qiao", None, [WATERLOO_STAT()]),
        ("Ngoc Hieu Tran", "nh2tran@uwaterloo.ca", [WATERLOO_CS()]),
        ("Lei Xin", "lxin@bioinfor.com", [BSI()]),
        ("Baozhen Shan", None, [BSI()]),
        ("Ming Li", None, [WATERLOO_CS()]),
        ("Ali Ghodsi", None, [WATERLOO_STAT()]),
    ]
    for i, (name, email, affs) in enumerate(authors_dnv2, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 54: DeepNovo-DIA
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DeepNovo-DIA", "https://github.com/nh2tran/DeepNovo-DIA")
    pub = create_publication(
        cur, "Deep learning enables de novo peptide sequencing from data-independent-acquisition mass spectrometry",
        "2018-12-20", "10.1038/s41592-018-0260-3", None,
        None, "https://doi.org/10.1038/s41592-018-0260-3", "Nature Methods", "peer-reviewed",
    )
    uw_ece_waterloo = A("University of Waterloo", "Department of Electrical & Computer Engineering", "Waterloo", "Canada")
    beijing_micro = A("Beijing Institute of Microbiology and Epidemiology", "State Key Laboratory of Pathogen and Biosecurity", "Beijing", "China")

    authors_dnovodia = [
        ("Ngoc Hieu Tran", None, [WATERLOO_CS()]),
        ("Rui Qiao", None, [WATERLOO_STAT()]),
    ]
    for i, (name, email, affs) in enumerate(authors_dnovodia, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 55: PostNovo
    # ===================================================================
    alg = get_or_create_algorithm(cur, "PostNovo")
    pub = create_publication(
        cur, "Postnovo: Postprocessing Enables Accurate and FDR-Controlled de Novo Peptide Sequencing",
        "2018-10-02", "10.1021/acs.jproteome.8b00278", None,
        None, "https://doi.org/10.1021/acs.jproteome.8b00278", "Journal of Proteome Research", "peer-reviewed",
    )
    uchicago = A("University of Chicago", "Department of the Geophysical Sciences", "Chicago", "USA")
    authors_postnovo = [
        ("Samuel E. Miller", None, [uchicago]),
        ("Adriana I. Rizzo", None, [uchicago]),
        ("Jacob R. Waldbauer", None, [uchicago]),
    ]
    for i, (name, email, affs) in enumerate(authors_postnovo, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    # ===================================================================
    # PAPER 56: DeepNovo V1
    # ===================================================================
    alg = get_or_create_algorithm(cur, "DeepNovo", "https://github.com/nh2tran/DeepNovo")
    pub = create_publication(
        cur, "De novo peptide sequencing by deep learning",
        "2017-07-18", "10.1073/pnas.1705691114", None,
        None, "https://doi.org/10.1073/pnas.1705691114", "PNAS", "peer-reviewed",
    )
    authors_dnv1 = [
        ("Ngoc Hieu Tran", None, [WATERLOO_CS()]),
        ("Xianglilan Zhang", None, [WATERLOO_CS(), beijing_micro]),
        ("Lei Xin", None, [BSI()]),
        ("Baozhen Shan", None, [BSI()]),
        ("Ming Li", None, [WATERLOO_CS()]),
    ]
    for i, (name, email, affs) in enumerate(authors_dnv1, 1):
        aid = get_or_create_author(cur, name, email)
        for af in affs:
            link_author_affiliation(cur, aid, af)
        link_pub_author(cur, pub, aid, i)
    link_pub_algorithm(cur, pub, alg)

    conn.commit()
    print("Done! All remaining articles inserted.")

    # Print summary
    cur.execute("SELECT COUNT(*) FROM algorithm")
    print(f"Total algorithms: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM publication")
    print(f"Total publications: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM author")
    print(f"Total authors: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM affiliation")
    print(f"Total affiliations: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM country")
    print(f"Total countries: {cur.fetchone()[0]}")


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    try:
        populate(conn)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
