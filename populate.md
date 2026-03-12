# Populate the database


```sql
%DELETE
```


```sql
%CREATE denovo.db
```


```sql
-- Table to store author information
CREATE TABLE author (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT
)
```


```sql
-- Table to store country information
CREATE TABLE country (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
)
```


```sql
-- Table to store city information
CREATE TABLE city (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    country_id INTEGER,
    FOREIGN KEY (country_id) REFERENCES country(id)
        ON DELETE CASCADE
        ON UPDATE CASCADE
)
```


```sql
-- Table to store affiliation information
CREATE TABLE affiliation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    department TEXT,
    country_id INTEGER,
    city_id INTEGER,
    UNIQUE (name, department),
    FOREIGN KEY (country_id) REFERENCES country(id)
        ON DELETE SET NULL
        ON UPDATE CASCADE,
    FOREIGN KEY (city_id) REFERENCES city(id)
        ON DELETE SET NULL
        ON UPDATE CASCADE
)
```


```sql
-- Junction table to associate authors with affiliations (many-to-many)
CREATE TABLE author_affiliation (
    author_id INTEGER, -- NOT NULL,
    affiliation_id INTEGER, -- NOT NULL,
    PRIMARY KEY (author_id, affiliation_id),
    FOREIGN KEY (author_id) REFERENCES author(id)
        ON DELETE CASCADE,
    FOREIGN KEY (affiliation_id) REFERENCES affiliation(id)
        ON DELETE CASCADE
)
```


```sql
-- Table to store algorithm information
CREATE TABLE algorithm (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    repository TEXT,
    documentation TEXT,
    website TEXT
)
```


```sql
-- table to store publication information
CREATE TABLE publication (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    publication_date DATE,
    doi TEXT UNIQUE,
    publisher TEXT,
    abstract TEXT,
    url TEXT,
    journal TEXT,
    publication_type TEXT NOT NULL -- 'preprint' or 'peer-reviewed'
);
```


```sql
-- Junction table to associate publications with algorithms (many-to-many)
CREATE TABLE publication_algorithm (
    publication_id INTEGER NOT NULL,
    algorithm_id INTEGER NOT NULL,
    PRIMARY KEY (publication_id, algorithm_id),
    FOREIGN KEY (publication_id) REFERENCES publication(id)
        ON DELETE CASCADE,
    FOREIGN KEY (algorithm_id) REFERENCES algorithm(id)
        ON DELETE CASCADE
)
```


```sql
-- Junction table to associate authors with publications (many-to-many)
CREATE TABLE publication_author (
    publication_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    author_order INTEGER NOT NULL,
    PRIMARY KEY (publication_id, author_id),
    FOREIGN KEY (publication_id) REFERENCES publication(id)
        ON DELETE CASCADE,
    FOREIGN KEY (author_id) REFERENCES author(id)
        ON DELETE CASCADE
)
```

# Insert Data


```sql
-- Insert new countries
INSERT INTO country (name) VALUES
('Belgium'),
('China'),
('Denmark'),
('Germany'),
('Netherlands'),
('Sweden'),
('UK')
```


```sql
-- Insert new cities
INSERT INTO city (name, country_id) VALUES
('London', (SELECT id FROM country WHERE name = 'UK')),
('Manchester', (SELECT id FROM country WHERE name = 'UK')),
('Copenhagen', (SELECT id FROM country WHERE name = 'Denmark')),
('Kongens Lyngby', (SELECT id FROM country WHERE name = 'Denmark')),
('Delft', (SELECT id FROM country WHERE name = 'Netherlands')),
('Freising', (SELECT id FROM country WHERE name = 'Germany')),
('Garching', (SELECT id FROM country WHERE name = 'Germany')),
('Stockholm', (SELECT id FROM country WHERE name = 'Sweden')),
('Beijing', (SELECT id FROM country WHERE name = 'China')),
('Shenzhen', (SELECT id FROM country WHERE name = 'China')),
('Shenyang', (SELECT id FROM country WHERE name = 'China')),
('Zibo', (SELECT id FROM country WHERE name = 'China')),
('Ghent', (SELECT id FROM country WHERE name = 'Belgium')),
('Brussels', (SELECT id FROM country WHERE name = 'Belgium'))

```


```sql
-- Insert new affiliations
INSERT INTO affiliation (department, name, city_id, country_id) VALUES
-- InstaNovo
(NULL, 'InstaDeep Ltd', (SELECT id FROM city WHERE name = 'London'), (SELECT id FROM country WHERE name = 'UK')),
('Department of Biotechnology and Biomedicine', 'Technical University of Denmark', (SELECT id FROM city WHERE name = 'Kongens Lyngby'), (SELECT id FROM country WHERE name = 'Denmark')),
('Novo Nordisk Foundation Center for Biosustainability', 'Technical University of Denmark', (SELECT id FROM city WHERE name = 'Kongens Lyngby'), (SELECT id FROM country WHERE name = 'Denmark')),
('Department of Bionanoscience', 'Delft University of Technology', (SELECT id FROM city WHERE name = 'Delft'), (SELECT id FROM country WHERE name = 'Netherlands')),
(NULL, 'Kavli Institute of Nanoscience', (SELECT id FROM city WHERE name = 'Delft'), (SELECT id FROM country WHERE name = 'Netherlands')),
-- InstaNovo-P
('Department of Applied Mathematics and Computer Science', 'Technical University of Denmark', (SELECT id FROM city WHERE name = 'Kongens Lyngby'), (SELECT id FROM country WHERE name = 'Denmark')),
('CompOmics, VIB Center for Medical Biotechnology', 'VIB', (SELECT id FROM city WHERE name = 'Ghent'), (SELECT id FROM country WHERE name = 'Belgium')),
('Department of Biomolecular Medicine, Faculty of Medicine and Health Sciences', 'Ghent University', (SELECT id FROM city WHERE name = 'Ghent'), (SELECT id FROM country WHERE name = 'Belgium')),
('Interuniversity Institute of Bioinformatics in Brussels', 'ULB-VUB', (SELECT id FROM city WHERE name = 'Brussels'), (SELECT id FROM country WHERE name = 'Belgium')),
('Structural Biology Brussels', 'Vrije Universiteit Brussel', (SELECT id FROM city WHERE name = 'Brussels'), (SELECT id FROM country WHERE name = 'Belgium')),
('Division of Molecular and Cellular Function, School of Biological Science, Faculty of Biology Medicine and Health
(FBMH)', 'The University of Manchester', (SELECT id FROM city WHERE name = 'Manchester'), (SELECT id FROM country WHERE name = 'UK')),
-- PairWise    
('Computational Mass Spectrometry, TUM School of Life Sciences', 'Technical University of Munich', (SELECT id FROM city WHERE name = 'Freising'), (SELECT id FROM country WHERE name = 'Germany')),
('Munich Data Science Institute', 'Technical University of Munich', (SELECT id FROM city WHERE name = 'Garching'), (SELECT id FROM country WHERE name = 'Germany')),
('Science for Life Laboratory', 'KTH - Royal Institute of Technology', (SELECT id FROM city WHERE name = 'Stockholm'), (SELECT id FROM country WHERE name = 'Sweden')),
-- DiNovo
('State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science', 'Chinese Academy of Sciences', (SELECT id FROM city WHERE name = 'Beijing'), (SELECT id FROM country WHERE name = 'China')),
('School of Computer Science and Technology', 'Shandong University of Technology', (SELECT id FROM city WHERE name = 'Zibo'), (SELECT id FROM country WHERE name = 'China')),
('State Key Laboratory of Medical Proteomics, National Center for Protein Sciences (Beijing),
Research Unit of Proteomics and Research and Development of New Drug of Chinese Academy of
Medical Sciences, Beijing Proteome Research Center', 'Beijing Institute of Lifeomics', (SELECT id FROM city WHERE name = 'Beijing'), (SELECT id FROM country WHERE name = 'China')),
('Key Laboratory of Intelligent Information Processing of Chinese Academy of Sciences, Institute of
Computing Technology', 'Chinese Academy of Sciences', (SELECT id FROM city WHERE name = 'Beijing'), (SELECT id FROM country WHERE name = 'China')),
(NULL, 'University of Chinese Academy of Sciences', (SELECT id FROM city WHERE name = 'Beijing'), (SELECT id FROM country WHERE name = 'China')),
('Program of Environmental Toxicology, School of Public Health', 'China Medical University', (SELECT id FROM city WHERE name = 'Shenyang'), (SELECT id FROM country WHERE name = 'China')),
-- PepGo
('Department of Biology', 'University of Copenhagen', (SELECT id FROM city WHERE name = 'Copenhagen'), (SELECT id FROM country WHERE name = 'Denmark')),
(NULL, 'BGI-Shenzhen', (SELECT id FROM city WHERE name = 'Shenzhen'), (SELECT id FROM country WHERE name = 'China'))
```


```sql
SELECT
    af.department AS department,
    af.name AS affiliation,
    ci.name AS city,
    c.name AS country
FROM 
    affiliation af
LEFT JOIN
    country c ON af.country_id = c.id
LEFT JOIN
    city ci ON af.city_id = ci.id
ORDER BY
    c.name, af.name
```




<table>
<tr>
<th>department</th>
<th>affiliation</th>
<th>city</th>
<th>country</th>
</tr>
<tr>
<td>Department of Biomolecular Medicine, Faculty of Medicine and Health Sciences</td>
<td>Ghent University</td>
<td>Ghent</td>
<td>Belgium</td>
</tr>
<tr>
<td>Interuniversity Institute of Bioinformatics in Brussels</td>
<td>ULB-VUB</td>
<td>Brussels</td>
<td>Belgium</td>
</tr>
<tr>
<td>CompOmics, VIB Center for Medical Biotechnology</td>
<td>VIB</td>
<td>Ghent</td>
<td>Belgium</td>
</tr>
<tr>
<td>Structural Biology Brussels</td>
<td>Vrije Universiteit Brussel</td>
<td>Brussels</td>
<td>Belgium</td>
</tr>
<tr>
<td></td>
<td>BGI-Shenzhen</td>
<td>Shenzhen</td>
<td>China</td>
</tr>
<tr>
<td>State Key Laboratory of Medical Proteomics, National Center for Protein Sciences (Beijing),
Research Unit of Proteomics and Research and Development of New Drug of Chinese Academy of
Medical Sciences, Beijing Proteome Research Center</td>
<td>Beijing Institute of Lifeomics</td>
<td>Beijing</td>
<td>China</td>
</tr>
<tr>
<td>Program of Environmental Toxicology, School of Public Health</td>
<td>China Medical University</td>
<td>Shenyang</td>
<td>China</td>
</tr>
<tr>
<td>State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science</td>
<td>Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
</tr>
<tr>
<td>Key Laboratory of Intelligent Information Processing of Chinese Academy of Sciences, Institute of
Computing Technology</td>
<td>Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
</tr>
<tr>
<td>School of Computer Science and Technology</td>
<td>Shandong University of Technology</td>
<td>Zibo</td>
<td>China</td>
</tr>
<tr>
<td></td>
<td>University of Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
</tr>
<tr>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
</tr>
<tr>
<td>Novo Nordisk Foundation Center for Biosustainability</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
</tr>
<tr>
<td>Department of Applied Mathematics and Computer Science</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
</tr>
<tr>
<td>Department of Biology</td>
<td>University of Copenhagen</td>
<td>Copenhagen</td>
<td>Denmark</td>
</tr>
<tr>
<td>Computational Mass Spectrometry, TUM School of Life Sciences</td>
<td>Technical University of Munich</td>
<td>Freising</td>
<td>Germany</td>
</tr>
<tr>
<td>Munich Data Science Institute</td>
<td>Technical University of Munich</td>
<td>Garching</td>
<td>Germany</td>
</tr>
<tr>
<td>Department of Bionanoscience</td>
<td>Delft University of Technology</td>
<td>Delft</td>
<td>Netherlands</td>
</tr>
<tr>
<td></td>
<td>Kavli Institute of Nanoscience</td>
<td>Delft</td>
<td>Netherlands</td>
</tr>
<tr>
<td>Science for Life Laboratory</td>
<td>KTH - Royal Institute of Technology</td>
<td>Stockholm</td>
<td>Sweden</td>
</tr>
<tr>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
</tr>
<tr>
<td>Division of Molecular and Cellular Function, School of Biological Science, Faculty of Biology Medicine and Health
(FBMH)</td>
<td>The University of Manchester</td>
<td>Manchester</td>
<td>UK</td>
</tr>
</table>




```sql
-- Insert authors
INSERT INTO author (name, email) VALUES
-- InstaNovo & InstaNovo+
('Kevin Eloff', 'k.eloff@instadeep.com'),
('Konstantinos Kalogeropoulos', 'konka@dtu.dk'),
('Amandla Mabona', NULL),
('Oliver Morell', NULL),
('Rachel Catzel', NULL),
('Esperanza Rivera-de-Torre', NULL),
('Jakob Berg Jespersen', NULL),
('Wesley Williams', NULL),
('Sam P. B. van Beljouw', NULL),
('Marcin J. Skwark', NULL),
('Andreas Hougaard Laustsen', NULL),
('Stan J. J. Brouns', NULL),
('Anne Ljungars', NULL),
('Erwin M. Schoof', NULL),
('Jeroen Van Goey', NULL),
('Ulrich auf dem Keller', NULL),
('Karim Beguir', NULL),
('Nicolas Lopez Carranza', NULL),
('Timothy P. Jenkins', 'tpaje@dtu.dk'),
-- InstaNovo-P
('Jesper Lauridsen', NULL),
('Pathmanaban Ramasamy', NULL),
('Vahap Canbay', NULL),
('Paul Fullwood', NULL),
('Jennifer Ferguson', NULL),
('Annekatrine Kirketerp-Møller', NULL),
('Ida Sofie Goldschmidt', NULL),
('Tine Claeys', NULL),
('Sam van Puyenbroeck', NULL),
('Lennart Martens', NULL),
('Chiara Francavilla', NULL),
-- PairWise
('Joel Lapin', NULL),
('Alfred Nilsson', NULL),
('Mathias Wilhelm', 'mathias.wilhelm@tum.de'),
('Lukas Käll', 'lukas.kall@scilifelab.se'),
-- DiNovo
('Zixuan Cao', NULL),
('Xueli Peng', NULL),
('Di Zhang', NULL),
('Piyu Zhou', NULL),
('Li Kang', NULL),
('Hao Chi', NULL),
('Ruitao Wu', NULL),
('Zhiyuan Cheng', NULL),
('Yao Zhang', NULL),
('Jiaxin Dai', NULL),
('Yanchang Li', NULL),
('Lijin Yao', NULL),
('Xinming Li', NULL),
('Jinghan Yang', NULL),
('Haipeng Wang', 'hpwang@sdut.edu.cn'),
('Ping Xu', 'xuping_bprc@126.com'),
('Yan Fu', 'yfu@amss.ac.cn'),
-- PepGo
('Yuqi Chang', NULL),
('Siqi Liu', 'siqiliu@genomics.cn'),
('Karsten Kristiansen', 'kk@bio.ku.dk')

```


```sql
-- Associate authors with affiliations
INSERT INTO author_affiliation (author_id, affiliation_id) VALUES
-- InstaNovo
((SELECT id FROM author WHERE name = 'Kevin Eloff'), (SELECT id FROM affiliation WHERE name = 'InstaDeep Ltd')),
((SELECT id FROM author WHERE name = 'Konstantinos Kalogeropoulos'), (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
((SELECT id FROM author WHERE name = 'Amandla Mabona'), (SELECT id FROM affiliation WHERE name = 'InstaDeep Ltd')),
((SELECT id FROM author WHERE name = 'Oliver Morell'), (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
((SELECT id FROM author WHERE name = 'Rachel Catzel'), (SELECT id FROM affiliation WHERE name = 'InstaDeep Ltd')),
((SELECT id FROM author WHERE name = 'Esperanza Rivera-de-Torre'), (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
((SELECT id FROM author WHERE name = 'Jakob Berg Jespersen'), (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Novo Nordisk Foundation Center for Biosustainability')),
((SELECT id FROM author WHERE name = 'Wesley Williams'), (SELECT id FROM affiliation WHERE name = 'InstaDeep Ltd')),
((SELECT id FROM author WHERE name = 'Sam P. B. van Beljouw'), (SELECT id FROM affiliation WHERE name = 'Delft University of Technology' AND department = 'Department of Bionanoscience')),
((SELECT id FROM author WHERE name = 'Sam P. B. van Beljouw'), (SELECT id FROM affiliation WHERE name = 'Kavli Institute of Nanoscience')),
((SELECT id FROM author WHERE name = 'Marcin J. Skwark'), (SELECT id FROM affiliation WHERE name = 'InstaDeep Ltd')),
((SELECT id FROM author WHERE name = 'Andreas Hougaard Laustsen'), (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
((SELECT id FROM author WHERE name = 'Stan J. J. Brouns'), (SELECT id FROM affiliation WHERE name = 'Delft University of Technology' AND department = 'Department of Bionanoscience')),
((SELECT id FROM author WHERE name = 'Stan J. J. Brouns'), (SELECT id FROM affiliation WHERE name = 'Kavli Institute of Nanoscience')),
((SELECT id FROM author WHERE name = 'Anne Ljungars'), (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
((SELECT id FROM author WHERE name = 'Erwin M. Schoof'), (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
((SELECT id FROM author WHERE name = 'Jeroen Van Goey'), (SELECT id FROM affiliation WHERE name = 'InstaDeep Ltd')),
((SELECT id FROM author WHERE name = 'Ulrich auf dem Keller'), (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
((SELECT id FROM author WHERE name = 'Karim Beguir'), (SELECT id FROM affiliation WHERE name = 'InstaDeep Ltd')),
((SELECT id FROM author WHERE name = 'Nicolas Lopez Carranza'), (SELECT id FROM affiliation WHERE name = 'InstaDeep Ltd')),
((SELECT id FROM author WHERE name = 'Timothy P. Jenkins'), (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
--InstaNovo-P
((SELECT id FROM author WHERE name = 'Jesper Lauridsen'),  (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
((SELECT id FROM author WHERE name = 'Jesper Lauridsen'),  (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Applied Mathematics and Computer Science')),
((SELECT id FROM author WHERE name = 'Pathmanaban Ramasamy'), (SELECT id FROM affiliation WHERE name = 'VIB' AND department = 'CompOmics, VIB Center for Medical Biotechnology')),
((SELECT id FROM author WHERE name = 'Pathmanaban Ramasamy'), (SELECT id FROM affiliation WHERE name = 'Ghent University' AND department = 'Department of Biomolecular Medicine, Faculty of Medicine and Health Sciences')),
((SELECT id FROM author WHERE name = 'Pathmanaban Ramasamy'), (SELECT id FROM affiliation WHERE name = 'ULB-VUB' AND department = 'Interuniversity Institute of Bioinformatics in Brussels')),
((SELECT id FROM author WHERE name = 'Pathmanaban Ramasamy'), (SELECT id FROM affiliation WHERE name = 'Vrije Universiteit Brussel' AND department = 'Structural Biology Brussels')),

((SELECT id FROM author WHERE name = 'Vahap Canbay'),  (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
((SELECT id FROM author WHERE name = 'Paul Fullwood'),  (SELECT id FROM affiliation WHERE name = 'The University of Manchester' AND department = 'Division of Molecular and Cellular Function, School of Biological Science, Faculty of Biology Medicine and Health
(FBMH)')),
((SELECT id FROM author WHERE name = 'Jennifer Ferguson'),  (SELECT id FROM affiliation WHERE name = 'The University of Manchester' AND department = 'Division of Molecular and Cellular Function, School of Biological Science, Faculty of Biology Medicine and Health
(FBMH)')),
((SELECT id FROM author WHERE name = 'Annekatrine Kirketerp-Møller'),  (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
((SELECT id FROM author WHERE name = 'Ida Sofie Goldschmidt'),  (SELECT id FROM affiliation WHERE name = 'Technical University of Denmark' AND department = 'Department of Biotechnology and Biomedicine')),
((SELECT id FROM author WHERE name = 'Tine Claeys'),  (SELECT id FROM affiliation WHERE name = 'VIB' AND department = 'CompOmics, VIB Center for Medical Biotechnology')),
((SELECT id FROM author WHERE name = 'Tine Claeys'),  (SELECT id FROM affiliation WHERE name = 'Ghent University' AND department = 'Department of Biomolecular Medicine, Faculty of Medicine and Health Sciences')),
((SELECT id FROM author WHERE name = 'Sam van Puyenbroeck'),  (SELECT id FROM affiliation WHERE name = 'VIB' AND department = 'CompOmics, VIB Center for Medical Biotechnology')),
((SELECT id FROM author WHERE name = 'Sam van Puyenbroeck'),  (SELECT id FROM affiliation WHERE name = 'Ghent University' AND department = 'Department of Biomolecular Medicine, Faculty of Medicine and Health Sciences')),
((SELECT id FROM author WHERE name = 'Lennart Martens'),  (SELECT id FROM affiliation WHERE name = 'VIB' AND department = 'CompOmics, VIB Center for Medical Biotechnology')),
((SELECT id FROM author WHERE name = 'Lennart Martens'), (SELECT id FROM affiliation WHERE name = 'Ghent University' AND department = 'Department of Biomolecular Medicine, Faculty of Medicine and Health Sciences')),
((SELECT id FROM author WHERE name = 'Chiara Francavilla'),  (SELECT id FROM affiliation WHERE name = 'The University of Manchester' AND department = 'Division of Molecular and Cellular Function, School of Biological Science, Faculty of Biology Medicine and Health
(FBMH)')),
-- PairWise
((SELECT id FROM author WHERE name = 'Joel Lapin'), (SELECT id FROM affiliation WHERE name = 'Technical University of Munich' AND department = 'Computational Mass Spectrometry, TUM School of Life Sciences')),
((SELECT id FROM author WHERE name = 'Alfred Nilsson'), (SELECT id FROM affiliation WHERE name = 'KTH - Royal Institute of Technology' AND department = 'Science for Life Laboratory')),
((SELECT id FROM author WHERE name = 'Mathias Wilhelm'), (SELECT id FROM affiliation WHERE name = 'Technical University of Munich' AND department = 'Computational Mass Spectrometry, TUM School of Life Sciences')),
((SELECT id FROM author WHERE name = 'Mathias Wilhelm'), (SELECT id FROM affiliation WHERE name = 'Technical University of Munich' AND department = 'Munich Data Science Institute')),
((SELECT id FROM author WHERE name = 'Lukas Käll'), (SELECT id FROM affiliation WHERE name = 'KTH - Royal Institute of Technology' AND department = 'Science for Life Laboratory')),
--DiNovo
((SELECT id FROM author WHERE name = 'Zixuan Cao'), (SELECT id FROM affiliation WHERE name = 'Chinese Academy of Sciences' AND department = 'State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science')),
((SELECT id FROM author WHERE name = 'Zixuan Cao'), (SELECT id FROM affiliation WHERE name = 'University of Chinese Academy of Sciences')),
((SELECT id FROM author WHERE name = 'Xueli Peng'), (SELECT id FROM affiliation WHERE name = 'Chinese Academy of Sciences' AND department = 'State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science')),
((SELECT id FROM author WHERE name = 'Xueli Peng'), (SELECT id FROM affiliation WHERE name = 'University of Chinese Academy of Sciences')),
((SELECT id FROM author WHERE name = 'Di Zhang'), (SELECT id FROM affiliation WHERE name = 'Shandong University of Technology')),
((SELECT id FROM author WHERE name = 'Piyu Zhou'), (SELECT id FROM affiliation WHERE name = 'Chinese Academy of Sciences' AND department = 'State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science')),
((SELECT id FROM author WHERE name = 'Piyu Zhou'), (SELECT id FROM affiliation WHERE name = 'University of Chinese Academy of Sciences')),
((SELECT id FROM author WHERE name = 'Li Kang'), (SELECT id FROM affiliation WHERE name = 'Beijing Institute of Lifeomics')),
((SELECT id FROM author WHERE name = 'Li Kang'), (SELECT id FROM affiliation WHERE name = 'China Medical University')),
((SELECT id FROM author WHERE name = 'Hao Chi'), (SELECT id FROM affiliation WHERE name = 'Chinese Academy of Sciences' AND department = 'Key Laboratory of Intelligent Information Processing of Chinese Academy of Sciences, Institute of
Computing Technology')),
((SELECT id FROM author WHERE name = 'Hao Chi'), (SELECT id FROM affiliation WHERE name = 'University of Chinese Academy of Sciences')),
((SELECT id FROM author WHERE name = 'Ruitao Wu'), (SELECT id FROM affiliation WHERE name = 'Shandong University of Technology')),
((SELECT id FROM author WHERE name = 'Zhiyuan Cheng'), (SELECT id FROM affiliation WHERE name = 'Chinese Academy of Sciences' AND department = 'State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science')),
((SELECT id FROM author WHERE name = 'Zhiyuan Cheng'), (SELECT id FROM affiliation WHERE name = 'University of Chinese Academy of Sciences')),
((SELECT id FROM author WHERE name = 'Yao Zhang'), (SELECT id FROM affiliation WHERE name = 'Beijing Institute of Lifeomics')),
((SELECT id FROM author WHERE name = 'Jiaxin Dai'), (SELECT id FROM affiliation WHERE name = 'Beijing Institute of Lifeomics')),
((SELECT id FROM author WHERE name = 'Yanchang Li'), (SELECT id FROM affiliation WHERE name = 'Beijing Institute of Lifeomics')),
((SELECT id FROM author WHERE name = 'Lijin Yao'), (SELECT id FROM affiliation WHERE name = 'Shandong University of Technology')),
((SELECT id FROM author WHERE name = 'Xinming Li'), (SELECT id FROM affiliation WHERE name = 'Shandong University of Technology')),
((SELECT id FROM author WHERE name = 'Jinghan Yang'), (SELECT id FROM affiliation WHERE name = 'Chinese Academy of Sciences' AND department = 'State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science')),
((SELECT id FROM author WHERE name = 'Jinghan Yang'), (SELECT id FROM affiliation WHERE name = 'University of Chinese Academy of Sciences')),
((SELECT id FROM author WHERE name = 'Haipeng Wang'), (SELECT id FROM affiliation WHERE name = 'Shandong University of Technology')),
((SELECT id FROM author WHERE name = 'Ping Xu'), (SELECT id FROM affiliation WHERE name = 'Beijing Institute of Lifeomics')),
((SELECT id FROM author WHERE name = 'Ping Xu'), (SELECT id FROM affiliation WHERE name = 'China Medical University')),
((SELECT id FROM author WHERE name = 'Yan Fu'), (SELECT id FROM affiliation WHERE name = 'Chinese Academy of Sciences' AND department = 'State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science')),
((SELECT id FROM author WHERE name = 'Yan Fu'), (SELECT id FROM affiliation WHERE name = 'University of Chinese Academy of Sciences')),
--PepGo
((SELECT id FROM author WHERE name = 'Yuqi Chang'), (SELECT id FROM affiliation WHERE name = 'University of Copenhagen' AND department = 'Department of Biology')),
((SELECT id FROM author WHERE name = 'Siqi Liu'), (SELECT id FROM affiliation WHERE name = 'BGI-Shenzhen')),
((SELECT id FROM author WHERE name = 'Karsten Kristiansen'), (SELECT id FROM affiliation WHERE name = 'University of Copenhagen' AND department = 'Department of Biology')),
((SELECT id FROM author WHERE name = 'Karsten Kristiansen'), (SELECT id FROM affiliation WHERE name = 'BGI-Shenzhen'))
```


```sql
-- find authors who have at least one NULL affiliation in the author_affiliation table and list all their affiliations 
-- (both NULL and non-NULL) to spot which ones are incomplete.
SELECT 
    a.id AS author_id,
    a.name AS author_name,
    aff.name AS affiliation_name
FROM author_affiliation aa
JOIN author a ON aa.author_id = a.id
LEFT JOIN affiliation aff ON aa.affiliation_id = aff.id
WHERE a.id IN (
    SELECT author_id
    FROM author_affiliation
    WHERE affiliation_id IS NULL
);
```




<table>
<tr>
<th>author_id</th>
<th>author_name</th>
<th>affiliation_name</th>
</tr>
</table>




```sql
-- Insert algorithms
INSERT INTO algorithm (name, repository, documentation, website) VALUES
('InstaNovo', 'https://github.com/instadeepai/instanovo', 'https://instadeepai.github.io/InstaNovo/', 'https://instanovo.ai/'),
('InstaNovo+', 'https://github.com/instadeepai/instanovo', 'https://instadeepai.github.io/InstaNovo/', 'https://instanovo.ai/'),
('InstaNovo-P', 'https://github.com/InstaDeepAI/InstaNovo-P', NULL, 'https://instanovo.ai/'),
('Pairwise',  'https://github.com/statisticalbiotechnology/pairwise', NULL, NULL),
('DiNovo', 'https://github.com/YanFuGroup/DiNovo', NULL, 'http://fugroup.amss.ac.cn/software/DiNovo/DiNovoIndex.html'),
('PepGo', 'https://github.com/alifare/PepGo/tree/main', NULL, NULL)

```

## InstaNovo & InstaNovo+


```sql
-- Insert InstaNovo preprint publication
INSERT INTO publication (title, publication_date, doi, publisher, abstract, url, journal, publication_type) VALUES
('De novo peptide sequencing with InstaNovo: Accurate, database-free peptide identification for large scale proteomics experiments', 
    '2023-08-30', 
    '10.1101/2023.08.30.555055', 
    'Cold Spring Harbor Laboratory', 
    'Bottom-up mass spectrometry-based proteomics is challenged by the task of identifying the peptide that generates a tandem mass spectrum. Traditional methods that rely on known peptide sequence databases are limited and may not be applicable in certain contexts. De novo peptide sequencing, which assigns peptide sequences to the spectra without prior information, is valuable for various biological applications; yet, due to a lack of accuracy, it remains challenging to apply this approach in many situations. Here, we introduce InstaNovo, a transformer neural network with the ability to translate fragment ion peaks into the sequence of amino acids that make up the studied peptide(s). The model was trained on 28 million labelled spectra matched to 742k human peptides from the ProteomeTools project. We demonstrate that InstaNovo outperforms current state-of-the-art methods on benchmark datasets and showcase its utility in several applications. Building upon human intuition, we also introduce InstaNovo+, a multinomial diffusion model that further improves performance by iterative refinement of predicted sequences. Using these models, we could de novo sequence antibody-based therapeutics with unprecedented coverage, discover novel peptides, and detect unreported organisms in different datasets, thereby expanding the scope and detection rate of proteomics searches. Finally, we could experimentally validate tryptic and non-tryptic peptides with targeted proteomics, demonstrating the fidelity of our predictions. Our models unlock a plethora of opportunities across different scientific domains, such as direct protein sequencing, immunopeptidomics, and exploration of the dark proteome.', 
    'https://www.biorxiv.org/content/10.1101/2023.08.30.555055v3', 
    'bioRxiv', 
    'preprint')
```


```sql
-- Associate authors with InstaNovo preprint publication
INSERT INTO publication_author (publication_id, author_id, author_order) VALUES
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Kevin Eloff'), 1),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Konstantinos Kalogeropoulos'), 2),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Oliver Morell'), 3),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Amandla Mabona'), 4),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Jakob Berg Jespersen'), 5),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Wesley Williams'), 6),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Sam P. B. van Beljouw'), 7),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Marcin J. Skwark'), 8),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Andreas Hougaard Laustsen'), 9),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Stan J. J. Brouns'), 10),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Anne Ljungars'), 11),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Erwin M. Schoof'), 12),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Jeroen Van Goey'), 13),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Ulrich auf dem Keller'), 14),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Karim Beguir'), 15),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Nicolas Lopez Carranza'), 16),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM author WHERE name = 'Timothy P. Jenkins'), 17)

```


```sql
-- Insert InstaNovo peer-reviewed publication
INSERT INTO publication (title, publication_date, doi, publisher, abstract, url, journal, publication_type) VALUES
('InstaNovo enables diffusion-powered de novo peptide sequencing in large-scale proteomics experiments', 
    '2025-04-01', 
    '10.1038/s42256-025-01019-5', 
    NULL, 
    'Mass spectrometry-based proteomics focuses on identifying the peptide that generates a tandem mass spectrum. Traditional methods rely on protein databases but are often limited or inapplicable in certain contexts. De novo peptide sequencing, which assigns peptide sequences to spectra without prior information, is valuable for diverse biological applications; however, owing to a lack of accuracy, it remains challenging to apply. Here we introduce InstaNovo, a transformer model that translates fragment ion peaks into peptide sequences. We demonstrate that InstaNovo outperforms state-of-the-art methods and showcase its utility in several applications. We also introduce InstaNovo+, a diffusion model that improves performance through iterative refinement of predicted sequences. Using these models, we achieve improved therapeutic sequencing coverage, discover novel peptides and detect unreported organisms in diverse datasets, thereby expanding the scope and detection rate of proteomics searches. Our models unlock opportunities across domains such as direct protein sequencing, immunopeptidomics and exploration of the dark proteome.', 
    'https://doi.org/10.1038/s42256-025-01019-5', 
    'Nature Machine Intelligence', 
    'peer-reviewed')

```


```sql
-- Associate authors with InstaNovo peer-reviewed publication
INSERT INTO publication_author (publication_id, author_id, author_order) VALUES
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Kevin Eloff'), 1),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Konstantinos Kalogeropoulos'), 2),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Amandla Mabona'), 3),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Oliver Morell'), 4),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Rachel Catzel'), 5),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Esperanza Rivera-de-Torre'), 6),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Jakob Berg Jespersen'), 7),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Wesley Williams'), 8),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Sam P. B. van Beljouw'), 9),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Marcin J. Skwark'), 10),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Andreas Hougaard Laustsen'), 11),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Stan J. J. Brouns'), 12),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Anne Ljungars'), 13),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Erwin M. Schoof'), 14),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Jeroen Van Goey'), 15),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Ulrich auf dem Keller'), 16),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Karim Beguir'), 17),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Nicolas Lopez Carranza'), 18),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM author WHERE name = 'Timothy P. Jenkins'), 19)

```


```sql
-- Associate InstaNovo algorithms with preprint and peer-reviewed publications
INSERT INTO publication_algorithm (publication_id, algorithm_id) VALUES
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM algorithm WHERE name = 'InstaNovo')),
((SELECT id FROM publication WHERE doi = '10.1101/2023.08.30.555055'), (SELECT id FROM algorithm WHERE name = 'InstaNovo+')),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM algorithm WHERE name = 'InstaNovo')),
((SELECT id FROM publication WHERE doi = '10.1038/s42256-025-01019-5'), (SELECT id FROM algorithm WHERE name = 'InstaNovo+'))
```

## InstaNovo-P


```sql
-- Insert the InstaNovo-P preprint information
INSERT INTO publication (title, publication_date, doi, publisher, abstract, url, journal, publication_type) VALUES (
    'InstaNovo-P: A de novo peptide sequencing model for phosphoproteomics',
    '2025-05-14',
    '10.1101/2025.05.14.654049',
    'Cold Spring Harbor Laboratory',
    "Phosphorylation, a crucial post-translational modification (PTM), plays a central role in cellular signaling and disease mechanisms. Mass spectrometry-based phosphoproteomics is widely used for system-wide characterization of phosphorylation events. However, traditional methods struggle with accurate phosphorylated site localization, complex search spaces, and detecting sequences outside the reference database. Advances in de novo peptide sequencing offer opportunities to address these limitations, but have yet to become integrated and adapted for phosphoproteomics datasets. Here, we present InstaNovo-P, a phosphorylation specific version of our transformer-based InstaNovo model, fine-tuned on extensive phosphoproteomics datasets. InstaNovo-P significantly surpasses existing methods in phosphorylated peptide detection and phosphorylated site localization accuracy across multiple datasets, including complex experimental scenarios. Our model robustly identifies peptides with single and multiple phosphorylated sites, effectively localizing phosphorylation events on serine, threonine, and tyrosine residues. We experimentally validate our model predictions by studying FGFR2 signaling, further demonstrating that InstaNovo-P uncovers phosphorylated sites previously missed by traditional database searches. These predictions align with critical biological processes, confirming the model's capacity to yield valuable biological insights. InstaNovo-P adds value to phosphoproteomics experiments by effectively identifying biologically relevant phosphorylation events without prior information, providing a powerful analytical tool for the dissection of signaling pathways.",
    'https://www.biorxiv.org/content/10.1101/2025.05.14.654049v1',
    'bioRxiv',
    'preprint')
```


```sql
-- Associate authors with the InstaNovo-P preprint
INSERT INTO publication_author (publication_id, author_id, author_order) VALUES
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Jesper Lauridsen'), 1),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Pathmanaban Ramasamy'), 2),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Rachel Catzel'), 3),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Vahap Canbay'), 4),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Amandla Mabona'), 5),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Kevin Eloff'), 6),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Paul Fullwood'), 7),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Jennifer Ferguson'), 8),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Annekatrine Kirketerp-Møller'), 9),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Ida Sofie Goldschmidt'), 10),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Tine Claeys'), 11),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Sam van Puyenbroeck'), 12),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Nicolas Lopez Carranza'), 13),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Erwin M. Schoof'), 14),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Lennart Martens'), 15),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Jeroen Van Goey'), 16),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Chiara Francavilla'), 17),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Timothy P. Jenkins'), 18),
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'), (SELECT id FROM author WHERE name = 'Konstantinos Kalogeropoulos'), 19)
```


```sql
SELECT name
FROM (
    SELECT 'Jesper Lauridsen' AS name UNION ALL
    SELECT 'Pathmanaban Ramasamy' UNION ALL
    SELECT 'Rachel Catzel' UNION ALL
    SELECT 'Vahap Canbay' UNION ALL
    SELECT 'Amandla Mabona' UNION ALL
    SELECT 'Kevin Eloff' UNION ALL
    SELECT 'Paul Fullwood' UNION ALL
    SELECT 'Jennifer Ferguson' UNION ALL
    SELECT 'Annekatrine Kirketerp-Møller' UNION ALL
    SELECT 'Ida Sofie Goldschmidt' UNION ALL
    SELECT 'Tine Claeys' UNION ALL
    SELECT 'Sam van Puyenbroeck' UNION ALL
    SELECT 'Nicolas Lopez Carranza' UNION ALL
    SELECT 'Erwin M. Schoof' UNION ALL
    SELECT 'Lennart Martens' UNION ALL
    SELECT 'Jeroen Van Goey' UNION ALL
    SELECT 'Chiara Francavilla' UNION ALL
    SELECT 'Timothy Patrick Jenkins' UNION ALL
    SELECT 'Konstantinos Kalogeropoulos'
) AS expected_names
WHERE name NOT IN (SELECT name FROM author);

```




<table>
<tr>
<th>name</th>
</tr>
<tr>
<td>Timothy Patrick Jenkins</td>
</tr>
</table>




```sql
-- Associate InstaNovo-P algorithm with preprint
INSERT INTO publication_algorithm (publication_id, algorithm_id) VALUES
((SELECT id FROM publication WHERE doi = '10.1101/2025.05.14.654049'),
 (SELECT id FROM algorithm WHERE name = 'InstaNovo-P'))
```

## PairWise


```sql
-- Insert the PairWise preprint information
INSERT INTO publication (title, publication_date, doi, publisher, abstract, url, journal, publication_type) VALUES
('Pairwise Attention: Leveraging Mass Differences to Enhance De Novo Sequencing of Mass Spectra', '2025-03-28', 
    '10.1101/2025.03.28.645943', 
    'Cold Spring Harbor Laboratory', 
    'A fundamental challenge in mass spectrometry-based proteomics is determining which peptide generated a given MS2 spectrum. Peptide sequencing typically relies on matching spectra against a known sequence database, which in some applications is not available. Deep learning-based de novo sequencing can address this limitation by directly predicting peptide sequences from MS2 data. We have seen the application of the transformer architecture to de novo sequencing produce state-of-the-art results on the so-called nine-species benchmark. In this study, we propose an improved transformer encoder inspired by the heuristics used in the manual interpretation of spectra. We modify the attention mechanism with a learned bias based on pairwise mass differences, termed Pairwise Attention (PA). Adding PA improves average peptide precision at 100\% coverage by 12.7\% (5.9 percentage points) over our base transformer on the original nine-species benchmark. We have also achieved a 7.4\% increase over the previously published model Casanovo. Our MS2 encoding strategy is largely orthogonal to other transformer-based models encoding MS2 spectra, enabling straightforward integration into existing deep-learning approaches. Our results show that integrating domain-specific knowledge into transformers boosts de novo sequencing performance.Competing Interest StatementThe authors have declared no competing interest.', 
    'https://www.biorxiv.org/content/10.1101/2025.03.28.645943v1', 
    'bioRxiv', 
    'preprint')
```


```sql
-- Associate authors with the Pairwise preprint
INSERT INTO publication_author (publication_id, author_id, author_order) VALUES
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.28.645943'), (SELECT id FROM author WHERE name = 'Joel Lapin'), 1),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.28.645943'), (SELECT id FROM author WHERE name = 'Alfred Nilsson'), 2),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.28.645943'), (SELECT id FROM author WHERE name = 'Mathias Wilhelm'), 3),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.28.645943'), (SELECT id FROM author WHERE name = 'Lukas Käll'), 4)
```


```sql
-- Associate PairWise algorithm with preprint
INSERT INTO publication_algorithm (publication_id, algorithm_id) VALUES
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.28.645943'), (SELECT id FROM algorithm WHERE name = 'Pairwise'))
```

## DiNovo


```sql
-- Insert the DiNovo preprint information
INSERT INTO publication (title, publication_date, doi, publisher, abstract, url, journal, publication_type) VALUES (
    'DiNovo: high-coverage, high-confidence de novo peptide sequencing using mirror proteases and deep learning',
    '2025-03-20',
    '10.1101/2025.03.20.643920',
    'Cold Spring Harbor Laboratory',
    'Despite the recent advancements driven by deep learning, de novo peptide sequencing is still constrained by incomplete peptide fragmentation and insufficient protein digestion in current single protease-based proteomic experiments. Here, we present a software system, named DiNovo, for high-coverage and confidence de novo peptide sequencing by leveraging the complementarity of mirror proteases. DiNovo is empowered by several innovative algorithms, including a mirror-spectra recognition algorithm independent of pre-sequencing, two sequencing algorithms based on deep learning and graph theory, respectively, and target-decoy mapping, a method for sequencing result evaluation free of prior peptide identification. Compared with the trypsin protease used alone, DiNovo using two pairs of mirror proteases led to two to three times high-confidence amino acids sequenced. Compared with previous single-protease de novo sequencing algorithms, DiNovo achieved much higher sequence coverages. DiNovo also showed great potential as a powerful complement or alternative to database search for peptide identification with quality control.Competing Interest StatementThe authors have declared no competing interest.',
    'https://www.biorxiv.org/content/10.1101/2025.03.20.643920v1',
    'bioRxiv',
    'preprint')
```


```sql
-- Associate authors with the DiNovo preprint
INSERT INTO publication_author (publication_id, author_id, author_order) VALUES
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Zixuan Cao'), 1),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Xueli Peng'), 2),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Di Zhang'), 3),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Piyu Zhou'), 4),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Li Kang'), 5),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Hao Chi'), 6),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Ruitao Wu'), 7),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Zhiyuan Cheng'), 8),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Yao Zhang'), 9),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Jiaxin Dai'), 10),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Yanchang Li'), 11),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Lijin Yao'), 12),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Xinming Li'), 13),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Jinghan Yang'), 14),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Haipeng Wang'), 15),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Ping Xu'), 16),
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM author WHERE name = 'Yan Fu'), 17)
```


```sql
-- Associate DiNovo algorithm with preprint
INSERT INTO publication_algorithm (publication_id, algorithm_id) VALUES
((SELECT id FROM publication WHERE doi = '10.1101/2025.03.20.643920'), (SELECT id FROM algorithm WHERE name = 'DiNovo'))
```

## PepGo


```sql
-- Insert the PepGo preprint information
INSERT INTO publication (title, publication_date, doi, publisher, abstract, url, journal, publication_type) VALUES (
    'PepGo: a deep learning and tree search-based model for de novo peptide sequencing',
    '2025-02-24',
    '10.1101/2025.02.24.640018',
    'Cold Spring Harbor Laboratory',
    'Identifying peptide sequences from tandem mass spectra is a fundamental problem in proteomics. Unlike search-based methods that rely on matching spectra to databases, de novo peptide sequencing determines peptides directly from mass spectra without any prior information. However, the design of models and algorithms for de novo peptide sequencing remains a challenge. Many de novo approaches leverage deep learning but primarily focus on the architecture of neural networks, paying less attention to search algorithms. We introduce PepGo, a de novo peptide sequencing model that integrates Transformer neural networks with Monte Carlo Tree Search (MCTS). PepGo predicts peptide sequences directly from mass spectra without databases, even without prior training. We show that PepGo surpasses existing methods, achieving state-of-the-art performance. To our knowledge, this is the first approach to combine deep learning with MCTS for de novo peptide sequencing, offering a powerful and adaptable solution for peptide identification in proteomics research. Competing Interest Statement The authors have declared no competing interest.',
    'https://www.biorxiv.org/content/10.1101/2025.02.24.640018v1',
    'bioRxiv',
    'preprint')

```


```sql
-- Associate authors with the PepGo preprint
INSERT INTO publication_author (publication_id, author_id, author_order) VALUES
((SELECT id FROM publication WHERE doi = '10.1101/2025.02.24.640018'), (SELECT id FROM author WHERE name = 'Yuqi Chang'), 1),
((SELECT id FROM publication WHERE doi = '10.1101/2025.02.24.640018'), (SELECT id FROM author WHERE name = 'Siqi Liu'), 2),
((SELECT id FROM publication WHERE doi = '10.1101/2025.02.24.640018'), (SELECT id FROM author WHERE name = 'Karsten Kristiansen'), 3)

```


```sql
-- Associate PepGo algorithm with preprint
INSERT INTO publication_algorithm (publication_id, algorithm_id) VALUES
((SELECT id FROM publication WHERE doi = '10.1101/2025.02.24.640018'), (SELECT id FROM algorithm WHERE name = 'PepGo'));

```


```sql

```


```sql
-- Query to show all author information joined with their affiliations
SELECT DISTINCT
    a.name AS author_name,
    af.department AS affiliation_department,
    af.name AS affiliation_name,
    ci.name AS affiliation_city,
    c.name AS affiliation_country,
    a.email,
    alg.name AS algorithm_name,
    pa.author_order
FROM
    author a
LEFT JOIN
    author_affiliation aa ON a.id = aa.author_id
LEFT JOIN
    affiliation af ON aa.affiliation_id = af.id
LEFT JOIN
    country c ON af.country_id = c.id
LEFT JOIN
    city ci ON af.city_id = ci.id
LEFT JOIN
    publication_author pa ON a.id = pa.author_id
LEFT JOIN
    publication_algorithm pa_alg ON pa.publication_id = pa_alg.publication_id
LEFT JOIN
    algorithm alg ON pa_alg.algorithm_id = alg.id
-- WHERE
--    c.name = 'China'
ORDER BY
    alg.name,
    pa.author_order
```




<table>
<tr>
<th>author_name</th>
<th>affiliation_department</th>
<th>affiliation_name</th>
<th>affiliation_city</th>
<th>affiliation_country</th>
<th>email</th>
<th>algorithm_name</th>
<th>author_order</th>
</tr>
<tr>
<td>Zixuan Cao</td>
<td>State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science</td>
<td>Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>1</td>
</tr>
<tr>
<td>Zixuan Cao</td>
<td></td>
<td>University of Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>1</td>
</tr>
<tr>
<td>Xueli Peng</td>
<td>State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science</td>
<td>Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>2</td>
</tr>
<tr>
<td>Xueli Peng</td>
<td></td>
<td>University of Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>2</td>
</tr>
<tr>
<td>Di Zhang</td>
<td>School of Computer Science and Technology</td>
<td>Shandong University of Technology</td>
<td>Zibo</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>3</td>
</tr>
<tr>
<td>Piyu Zhou</td>
<td>State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science</td>
<td>Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>4</td>
</tr>
<tr>
<td>Piyu Zhou</td>
<td></td>
<td>University of Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>4</td>
</tr>
<tr>
<td>Li Kang</td>
<td>State Key Laboratory of Medical Proteomics, National Center for Protein Sciences (Beijing),
Research Unit of Proteomics and Research and Development of New Drug of Chinese Academy of
Medical Sciences, Beijing Proteome Research Center</td>
<td>Beijing Institute of Lifeomics</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>5</td>
</tr>
<tr>
<td>Li Kang</td>
<td>Program of Environmental Toxicology, School of Public Health</td>
<td>China Medical University</td>
<td>Shenyang</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>5</td>
</tr>
<tr>
<td>Hao Chi</td>
<td>Key Laboratory of Intelligent Information Processing of Chinese Academy of Sciences, Institute of
Computing Technology</td>
<td>Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>6</td>
</tr>
<tr>
<td>Hao Chi</td>
<td></td>
<td>University of Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>6</td>
</tr>
<tr>
<td>Ruitao Wu</td>
<td>School of Computer Science and Technology</td>
<td>Shandong University of Technology</td>
<td>Zibo</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>7</td>
</tr>
<tr>
<td>Zhiyuan Cheng</td>
<td>State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science</td>
<td>Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>8</td>
</tr>
<tr>
<td>Zhiyuan Cheng</td>
<td></td>
<td>University of Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>8</td>
</tr>
<tr>
<td>Yao Zhang</td>
<td>State Key Laboratory of Medical Proteomics, National Center for Protein Sciences (Beijing),
Research Unit of Proteomics and Research and Development of New Drug of Chinese Academy of
Medical Sciences, Beijing Proteome Research Center</td>
<td>Beijing Institute of Lifeomics</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>9</td>
</tr>
<tr>
<td>Jiaxin Dai</td>
<td>State Key Laboratory of Medical Proteomics, National Center for Protein Sciences (Beijing),
Research Unit of Proteomics and Research and Development of New Drug of Chinese Academy of
Medical Sciences, Beijing Proteome Research Center</td>
<td>Beijing Institute of Lifeomics</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>10</td>
</tr>
<tr>
<td>Yanchang Li</td>
<td>State Key Laboratory of Medical Proteomics, National Center for Protein Sciences (Beijing),
Research Unit of Proteomics and Research and Development of New Drug of Chinese Academy of
Medical Sciences, Beijing Proteome Research Center</td>
<td>Beijing Institute of Lifeomics</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>11</td>
</tr>
<tr>
<td>Lijin Yao</td>
<td>School of Computer Science and Technology</td>
<td>Shandong University of Technology</td>
<td>Zibo</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>12</td>
</tr>
<tr>
<td>Xinming Li</td>
<td>School of Computer Science and Technology</td>
<td>Shandong University of Technology</td>
<td>Zibo</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>13</td>
</tr>
<tr>
<td>Jinghan Yang</td>
<td>State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science</td>
<td>Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>14</td>
</tr>
<tr>
<td>Jinghan Yang</td>
<td></td>
<td>University of Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td></td>
<td>DiNovo</td>
<td>14</td>
</tr>
<tr>
<td>Haipeng Wang</td>
<td>School of Computer Science and Technology</td>
<td>Shandong University of Technology</td>
<td>Zibo</td>
<td>China</td>
<td>hpwang@sdut.edu.cn</td>
<td>DiNovo</td>
<td>15</td>
</tr>
<tr>
<td>Ping Xu</td>
<td>State Key Laboratory of Medical Proteomics, National Center for Protein Sciences (Beijing),
Research Unit of Proteomics and Research and Development of New Drug of Chinese Academy of
Medical Sciences, Beijing Proteome Research Center</td>
<td>Beijing Institute of Lifeomics</td>
<td>Beijing</td>
<td>China</td>
<td>xuping_bprc@126.com</td>
<td>DiNovo</td>
<td>16</td>
</tr>
<tr>
<td>Ping Xu</td>
<td>Program of Environmental Toxicology, School of Public Health</td>
<td>China Medical University</td>
<td>Shenyang</td>
<td>China</td>
<td>xuping_bprc@126.com</td>
<td>DiNovo</td>
<td>16</td>
</tr>
<tr>
<td>Yan Fu</td>
<td>State Key Laboratory of Mathematical Science, Academy of Mathematics and Systems Science</td>
<td>Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td>yfu@amss.ac.cn</td>
<td>DiNovo</td>
<td>17</td>
</tr>
<tr>
<td>Yan Fu</td>
<td></td>
<td>University of Chinese Academy of Sciences</td>
<td>Beijing</td>
<td>China</td>
<td>yfu@amss.ac.cn</td>
<td>DiNovo</td>
<td>17</td>
</tr>
<tr>
<td>Kevin Eloff</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td>k.eloff@instadeep.com</td>
<td>InstaNovo</td>
<td>1</td>
</tr>
<tr>
<td>Konstantinos Kalogeropoulos</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td>konka@dtu.dk</td>
<td>InstaNovo</td>
<td>2</td>
</tr>
<tr>
<td>Amandla Mabona</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>3</td>
</tr>
<tr>
<td>Oliver Morell</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>3</td>
</tr>
<tr>
<td>Amandla Mabona</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>4</td>
</tr>
<tr>
<td>Oliver Morell</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>4</td>
</tr>
<tr>
<td>Rachel Catzel</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>5</td>
</tr>
<tr>
<td>Jakob Berg Jespersen</td>
<td>Novo Nordisk Foundation Center for Biosustainability</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>5</td>
</tr>
<tr>
<td>Esperanza Rivera-de-Torre</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>6</td>
</tr>
<tr>
<td>Wesley Williams</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>6</td>
</tr>
<tr>
<td>Jakob Berg Jespersen</td>
<td>Novo Nordisk Foundation Center for Biosustainability</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>7</td>
</tr>
<tr>
<td>Sam P. B. van Beljouw</td>
<td>Department of Bionanoscience</td>
<td>Delft University of Technology</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo</td>
<td>7</td>
</tr>
<tr>
<td>Sam P. B. van Beljouw</td>
<td></td>
<td>Kavli Institute of Nanoscience</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo</td>
<td>7</td>
</tr>
<tr>
<td>Wesley Williams</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>8</td>
</tr>
<tr>
<td>Marcin J. Skwark</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>8</td>
</tr>
<tr>
<td>Sam P. B. van Beljouw</td>
<td>Department of Bionanoscience</td>
<td>Delft University of Technology</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo</td>
<td>9</td>
</tr>
<tr>
<td>Sam P. B. van Beljouw</td>
<td></td>
<td>Kavli Institute of Nanoscience</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo</td>
<td>9</td>
</tr>
<tr>
<td>Andreas Hougaard Laustsen</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>9</td>
</tr>
<tr>
<td>Marcin J. Skwark</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>10</td>
</tr>
<tr>
<td>Stan J. J. Brouns</td>
<td>Department of Bionanoscience</td>
<td>Delft University of Technology</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo</td>
<td>10</td>
</tr>
<tr>
<td>Stan J. J. Brouns</td>
<td></td>
<td>Kavli Institute of Nanoscience</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo</td>
<td>10</td>
</tr>
<tr>
<td>Andreas Hougaard Laustsen</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>11</td>
</tr>
<tr>
<td>Anne Ljungars</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>11</td>
</tr>
<tr>
<td>Stan J. J. Brouns</td>
<td>Department of Bionanoscience</td>
<td>Delft University of Technology</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo</td>
<td>12</td>
</tr>
<tr>
<td>Stan J. J. Brouns</td>
<td></td>
<td>Kavli Institute of Nanoscience</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo</td>
<td>12</td>
</tr>
<tr>
<td>Erwin M. Schoof</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>12</td>
</tr>
<tr>
<td>Anne Ljungars</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>13</td>
</tr>
<tr>
<td>Jeroen Van Goey</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>13</td>
</tr>
<tr>
<td>Erwin M. Schoof</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>14</td>
</tr>
<tr>
<td>Ulrich auf dem Keller</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>14</td>
</tr>
<tr>
<td>Jeroen Van Goey</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>15</td>
</tr>
<tr>
<td>Karim Beguir</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>15</td>
</tr>
<tr>
<td>Ulrich auf dem Keller</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo</td>
<td>16</td>
</tr>
<tr>
<td>Nicolas Lopez Carranza</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>16</td>
</tr>
<tr>
<td>Karim Beguir</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>17</td>
</tr>
<tr>
<td>Timothy P. Jenkins</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td>tpaje@dtu.dk</td>
<td>InstaNovo</td>
<td>17</td>
</tr>
<tr>
<td>Nicolas Lopez Carranza</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo</td>
<td>18</td>
</tr>
<tr>
<td>Timothy P. Jenkins</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td>tpaje@dtu.dk</td>
<td>InstaNovo</td>
<td>19</td>
</tr>
<tr>
<td>Kevin Eloff</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td>k.eloff@instadeep.com</td>
<td>InstaNovo+</td>
<td>1</td>
</tr>
<tr>
<td>Konstantinos Kalogeropoulos</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td>konka@dtu.dk</td>
<td>InstaNovo+</td>
<td>2</td>
</tr>
<tr>
<td>Amandla Mabona</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>3</td>
</tr>
<tr>
<td>Oliver Morell</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>3</td>
</tr>
<tr>
<td>Amandla Mabona</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>4</td>
</tr>
<tr>
<td>Oliver Morell</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>4</td>
</tr>
<tr>
<td>Rachel Catzel</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>5</td>
</tr>
<tr>
<td>Jakob Berg Jespersen</td>
<td>Novo Nordisk Foundation Center for Biosustainability</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>5</td>
</tr>
<tr>
<td>Esperanza Rivera-de-Torre</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>6</td>
</tr>
<tr>
<td>Wesley Williams</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>6</td>
</tr>
<tr>
<td>Jakob Berg Jespersen</td>
<td>Novo Nordisk Foundation Center for Biosustainability</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>7</td>
</tr>
<tr>
<td>Sam P. B. van Beljouw</td>
<td>Department of Bionanoscience</td>
<td>Delft University of Technology</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo+</td>
<td>7</td>
</tr>
<tr>
<td>Sam P. B. van Beljouw</td>
<td></td>
<td>Kavli Institute of Nanoscience</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo+</td>
<td>7</td>
</tr>
<tr>
<td>Wesley Williams</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>8</td>
</tr>
<tr>
<td>Marcin J. Skwark</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>8</td>
</tr>
<tr>
<td>Sam P. B. van Beljouw</td>
<td>Department of Bionanoscience</td>
<td>Delft University of Technology</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo+</td>
<td>9</td>
</tr>
<tr>
<td>Sam P. B. van Beljouw</td>
<td></td>
<td>Kavli Institute of Nanoscience</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo+</td>
<td>9</td>
</tr>
<tr>
<td>Andreas Hougaard Laustsen</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>9</td>
</tr>
<tr>
<td>Marcin J. Skwark</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>10</td>
</tr>
<tr>
<td>Stan J. J. Brouns</td>
<td>Department of Bionanoscience</td>
<td>Delft University of Technology</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo+</td>
<td>10</td>
</tr>
<tr>
<td>Stan J. J. Brouns</td>
<td></td>
<td>Kavli Institute of Nanoscience</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo+</td>
<td>10</td>
</tr>
<tr>
<td>Andreas Hougaard Laustsen</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>11</td>
</tr>
<tr>
<td>Anne Ljungars</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>11</td>
</tr>
<tr>
<td>Stan J. J. Brouns</td>
<td>Department of Bionanoscience</td>
<td>Delft University of Technology</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo+</td>
<td>12</td>
</tr>
<tr>
<td>Stan J. J. Brouns</td>
<td></td>
<td>Kavli Institute of Nanoscience</td>
<td>Delft</td>
<td>Netherlands</td>
<td></td>
<td>InstaNovo+</td>
<td>12</td>
</tr>
<tr>
<td>Erwin M. Schoof</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>12</td>
</tr>
<tr>
<td>Anne Ljungars</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>13</td>
</tr>
<tr>
<td>Jeroen Van Goey</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>13</td>
</tr>
<tr>
<td>Erwin M. Schoof</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>14</td>
</tr>
<tr>
<td>Ulrich auf dem Keller</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>14</td>
</tr>
<tr>
<td>Jeroen Van Goey</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>15</td>
</tr>
<tr>
<td>Karim Beguir</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>15</td>
</tr>
<tr>
<td>Ulrich auf dem Keller</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo+</td>
<td>16</td>
</tr>
<tr>
<td>Nicolas Lopez Carranza</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>16</td>
</tr>
<tr>
<td>Karim Beguir</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>17</td>
</tr>
<tr>
<td>Timothy P. Jenkins</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td>tpaje@dtu.dk</td>
<td>InstaNovo+</td>
<td>17</td>
</tr>
<tr>
<td>Nicolas Lopez Carranza</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo+</td>
<td>18</td>
</tr>
<tr>
<td>Timothy P. Jenkins</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td>tpaje@dtu.dk</td>
<td>InstaNovo+</td>
<td>19</td>
</tr>
<tr>
<td>Jesper Lauridsen</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo-P</td>
<td>1</td>
</tr>
<tr>
<td>Jesper Lauridsen</td>
<td>Department of Applied Mathematics and Computer Science</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo-P</td>
<td>1</td>
</tr>
<tr>
<td>Pathmanaban Ramasamy</td>
<td>CompOmics, VIB Center for Medical Biotechnology</td>
<td>VIB</td>
<td>Ghent</td>
<td>Belgium</td>
<td></td>
<td>InstaNovo-P</td>
<td>2</td>
</tr>
<tr>
<td>Pathmanaban Ramasamy</td>
<td>Department of Biomolecular Medicine, Faculty of Medicine and Health Sciences</td>
<td>Ghent University</td>
<td>Ghent</td>
<td>Belgium</td>
<td></td>
<td>InstaNovo-P</td>
<td>2</td>
</tr>
<tr>
<td>Pathmanaban Ramasamy</td>
<td>Interuniversity Institute of Bioinformatics in Brussels</td>
<td>ULB-VUB</td>
<td>Brussels</td>
<td>Belgium</td>
<td></td>
<td>InstaNovo-P</td>
<td>2</td>
</tr>
<tr>
<td>Pathmanaban Ramasamy</td>
<td>Structural Biology Brussels</td>
<td>Vrije Universiteit Brussel</td>
<td>Brussels</td>
<td>Belgium</td>
<td></td>
<td>InstaNovo-P</td>
<td>2</td>
</tr>
<tr>
<td>Rachel Catzel</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo-P</td>
<td>3</td>
</tr>
<tr>
<td>Vahap Canbay</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo-P</td>
<td>4</td>
</tr>
<tr>
<td>Amandla Mabona</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo-P</td>
<td>5</td>
</tr>
<tr>
<td>Kevin Eloff</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td>k.eloff@instadeep.com</td>
<td>InstaNovo-P</td>
<td>6</td>
</tr>
<tr>
<td>Paul Fullwood</td>
<td>Division of Molecular and Cellular Function, School of Biological Science, Faculty of Biology Medicine and Health
(FBMH)</td>
<td>The University of Manchester</td>
<td>Manchester</td>
<td>UK</td>
<td></td>
<td>InstaNovo-P</td>
<td>7</td>
</tr>
<tr>
<td>Jennifer Ferguson</td>
<td>Division of Molecular and Cellular Function, School of Biological Science, Faculty of Biology Medicine and Health
(FBMH)</td>
<td>The University of Manchester</td>
<td>Manchester</td>
<td>UK</td>
<td></td>
<td>InstaNovo-P</td>
<td>8</td>
</tr>
<tr>
<td>Annekatrine Kirketerp-Møller</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo-P</td>
<td>9</td>
</tr>
<tr>
<td>Ida Sofie Goldschmidt</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo-P</td>
<td>10</td>
</tr>
<tr>
<td>Tine Claeys</td>
<td>CompOmics, VIB Center for Medical Biotechnology</td>
<td>VIB</td>
<td>Ghent</td>
<td>Belgium</td>
<td></td>
<td>InstaNovo-P</td>
<td>11</td>
</tr>
<tr>
<td>Tine Claeys</td>
<td>Department of Biomolecular Medicine, Faculty of Medicine and Health Sciences</td>
<td>Ghent University</td>
<td>Ghent</td>
<td>Belgium</td>
<td></td>
<td>InstaNovo-P</td>
<td>11</td>
</tr>
<tr>
<td>Sam van Puyenbroeck</td>
<td>CompOmics, VIB Center for Medical Biotechnology</td>
<td>VIB</td>
<td>Ghent</td>
<td>Belgium</td>
<td></td>
<td>InstaNovo-P</td>
<td>12</td>
</tr>
<tr>
<td>Sam van Puyenbroeck</td>
<td>Department of Biomolecular Medicine, Faculty of Medicine and Health Sciences</td>
<td>Ghent University</td>
<td>Ghent</td>
<td>Belgium</td>
<td></td>
<td>InstaNovo-P</td>
<td>12</td>
</tr>
<tr>
<td>Nicolas Lopez Carranza</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo-P</td>
<td>13</td>
</tr>
<tr>
<td>Erwin M. Schoof</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td></td>
<td>InstaNovo-P</td>
<td>14</td>
</tr>
<tr>
<td>Lennart Martens</td>
<td>CompOmics, VIB Center for Medical Biotechnology</td>
<td>VIB</td>
<td>Ghent</td>
<td>Belgium</td>
<td></td>
<td>InstaNovo-P</td>
<td>15</td>
</tr>
<tr>
<td>Lennart Martens</td>
<td>Department of Biomolecular Medicine, Faculty of Medicine and Health Sciences</td>
<td>Ghent University</td>
<td>Ghent</td>
<td>Belgium</td>
<td></td>
<td>InstaNovo-P</td>
<td>15</td>
</tr>
<tr>
<td>Jeroen Van Goey</td>
<td></td>
<td>InstaDeep Ltd</td>
<td>London</td>
<td>UK</td>
<td></td>
<td>InstaNovo-P</td>
<td>16</td>
</tr>
<tr>
<td>Chiara Francavilla</td>
<td>Division of Molecular and Cellular Function, School of Biological Science, Faculty of Biology Medicine and Health
(FBMH)</td>
<td>The University of Manchester</td>
<td>Manchester</td>
<td>UK</td>
<td></td>
<td>InstaNovo-P</td>
<td>17</td>
</tr>
<tr>
<td>Timothy P. Jenkins</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td>tpaje@dtu.dk</td>
<td>InstaNovo-P</td>
<td>18</td>
</tr>
<tr>
<td>Konstantinos Kalogeropoulos</td>
<td>Department of Biotechnology and Biomedicine</td>
<td>Technical University of Denmark</td>
<td>Kongens Lyngby</td>
<td>Denmark</td>
<td>konka@dtu.dk</td>
<td>InstaNovo-P</td>
<td>19</td>
</tr>
<tr>
<td>Joel Lapin</td>
<td>Computational Mass Spectrometry, TUM School of Life Sciences</td>
<td>Technical University of Munich</td>
<td>Freising</td>
<td>Germany</td>
<td></td>
<td>Pairwise</td>
<td>1</td>
</tr>
<tr>
<td>Alfred Nilsson</td>
<td>Science for Life Laboratory</td>
<td>KTH - Royal Institute of Technology</td>
<td>Stockholm</td>
<td>Sweden</td>
<td></td>
<td>Pairwise</td>
<td>2</td>
</tr>
<tr>
<td>Mathias Wilhelm</td>
<td>Computational Mass Spectrometry, TUM School of Life Sciences</td>
<td>Technical University of Munich</td>
<td>Freising</td>
<td>Germany</td>
<td>mathias.wilhelm@tum.de</td>
<td>Pairwise</td>
<td>3</td>
</tr>
<tr>
<td>Mathias Wilhelm</td>
<td>Munich Data Science Institute</td>
<td>Technical University of Munich</td>
<td>Garching</td>
<td>Germany</td>
<td>mathias.wilhelm@tum.de</td>
<td>Pairwise</td>
<td>3</td>
</tr>
<tr>
<td>Lukas Käll</td>
<td>Science for Life Laboratory</td>
<td>KTH - Royal Institute of Technology</td>
<td>Stockholm</td>
<td>Sweden</td>
<td>lukas.kall@scilifelab.se</td>
<td>Pairwise</td>
<td>4</td>
</tr>
<tr>
<td>Yuqi Chang</td>
<td>Department of Biology</td>
<td>University of Copenhagen</td>
<td>Copenhagen</td>
<td>Denmark</td>
<td></td>
<td>PepGo</td>
<td>1</td>
</tr>
<tr>
<td>Siqi Liu</td>
<td></td>
<td>BGI-Shenzhen</td>
<td>Shenzhen</td>
<td>China</td>
<td>siqiliu@genomics.cn</td>
<td>PepGo</td>
<td>2</td>
</tr>
<tr>
<td>Karsten Kristiansen</td>
<td>Department of Biology</td>
<td>University of Copenhagen</td>
<td>Copenhagen</td>
<td>Denmark</td>
<td>kk@bio.ku.dk</td>
<td>PepGo</td>
<td>3</td>
</tr>
<tr>
<td>Karsten Kristiansen</td>
<td></td>
<td>BGI-Shenzhen</td>
<td>Shenzhen</td>
<td>China</td>
<td>kk@bio.ku.dk</td>
<td>PepGo</td>
<td>3</td>
</tr>
</table>




```sql
-- Query to show for each algorithm the title and the type of its associated publications
SELECT
    alg.name AS algorithm_name,
    p.title AS publication_title,
    p.publication_type,
    p.journal,
    alg.repository
FROM
    algorithm AS alg
JOIN
    publication_algorithm AS pa ON alg.id = pa.algorithm_id
JOIN
    publication AS p ON pa.publication_id = p.id
ORDER BY
    p.publication_type,
    p.title,
    alg.name
```




<table>
<tr>
<th>algorithm_name</th>
<th>publication_title</th>
<th>publication_type</th>
<th>journal</th>
<th>repository</th>
</tr>
<tr>
<td>InstaNovo</td>
<td>InstaNovo enables diffusion-powered de novo peptide sequencing in large-scale proteomics experiments</td>
<td>peer-reviewed</td>
<td>Nature Machine Intelligence</td>
<td>https://github.com/instadeepai/instanovo</td>
</tr>
<tr>
<td>InstaNovo+</td>
<td>InstaNovo enables diffusion-powered de novo peptide sequencing in large-scale proteomics experiments</td>
<td>peer-reviewed</td>
<td>Nature Machine Intelligence</td>
<td>https://github.com/instadeepai/instanovo</td>
</tr>
<tr>
<td>InstaNovo</td>
<td>De novo peptide sequencing with InstaNovo: Accurate, database-free peptide identification for large scale proteomics experiments</td>
<td>preprint</td>
<td>bioRxiv</td>
<td>https://github.com/instadeepai/instanovo</td>
</tr>
<tr>
<td>InstaNovo+</td>
<td>De novo peptide sequencing with InstaNovo: Accurate, database-free peptide identification for large scale proteomics experiments</td>
<td>preprint</td>
<td>bioRxiv</td>
<td>https://github.com/instadeepai/instanovo</td>
</tr>
<tr>
<td>DiNovo</td>
<td>DiNovo: high-coverage, high-confidence de novo peptide sequencing using mirror proteases and deep learning</td>
<td>preprint</td>
<td>bioRxiv</td>
<td>https://github.com/YanFuGroup/DiNovo</td>
</tr>
<tr>
<td>InstaNovo-P</td>
<td>InstaNovo-P: A de novo peptide sequencing model for phosphoproteomics</td>
<td>preprint</td>
<td>bioRxiv</td>
<td>https://github.com/InstaDeepAI/InstaNovo-P</td>
</tr>
<tr>
<td>Pairwise</td>
<td>Pairwise Attention: Leveraging Mass Differences to Enhance De Novo Sequencing of Mass Spectra</td>
<td>preprint</td>
<td>bioRxiv</td>
<td>https://github.com/statisticalbiotechnology/pairwise</td>
</tr>
<tr>
<td>PepGo</td>
<td>PepGo: a deep learning and tree search-based model for de novo peptide sequencing</td>
<td>preprint</td>
<td>bioRxiv</td>
<td>https://github.com/alifare/PepGo/tree/main</td>
</tr>
</table>




```sql
SELECT
    a.name AS algorithm_name,
    p.title,
    p.publication_date,
    p.journal,
    p.publication_type,
    p.doi,
    p.publisher,
    -- p.abstract,
    p.url
FROM
    algorithm a
JOIN
    publication_algorithm pa ON a.id = pa.algorithm_id
JOIN
    publication p ON pa.publication_id = p.id
-- WHERE
--    a.name = 'DiNovo'
```




<table>
<tr>
<th>algorithm_name</th>
<th>title</th>
<th>publication_date</th>
<th>journal</th>
<th>publication_type</th>
<th>doi</th>
<th>publisher</th>
<th>url</th>
</tr>
<tr>
<td>InstaNovo</td>
<td>De novo peptide sequencing with InstaNovo: Accurate, database-free peptide identification for large scale proteomics experiments</td>
<td>2023-08-30</td>
<td>bioRxiv</td>
<td>preprint</td>
<td>10.1101/2023.08.30.555055</td>
<td>Cold Spring Harbor Laboratory</td>
<td>https://www.biorxiv.org/content/10.1101/2023.08.30.555055v3</td>
</tr>
<tr>
<td>InstaNovo+</td>
<td>De novo peptide sequencing with InstaNovo: Accurate, database-free peptide identification for large scale proteomics experiments</td>
<td>2023-08-30</td>
<td>bioRxiv</td>
<td>preprint</td>
<td>10.1101/2023.08.30.555055</td>
<td>Cold Spring Harbor Laboratory</td>
<td>https://www.biorxiv.org/content/10.1101/2023.08.30.555055v3</td>
</tr>
<tr>
<td>InstaNovo</td>
<td>InstaNovo enables diffusion-powered de novo peptide sequencing in large-scale proteomics experiments</td>
<td>2025-04-01</td>
<td>Nature Machine Intelligence</td>
<td>peer-reviewed</td>
<td>10.1038/s42256-025-01019-5</td>
<td></td>
<td>https://doi.org/10.1038/s42256-025-01019-5</td>
</tr>
<tr>
<td>InstaNovo+</td>
<td>InstaNovo enables diffusion-powered de novo peptide sequencing in large-scale proteomics experiments</td>
<td>2025-04-01</td>
<td>Nature Machine Intelligence</td>
<td>peer-reviewed</td>
<td>10.1038/s42256-025-01019-5</td>
<td></td>
<td>https://doi.org/10.1038/s42256-025-01019-5</td>
</tr>
<tr>
<td>InstaNovo-P</td>
<td>InstaNovo-P: A de novo peptide sequencing model for phosphoproteomics</td>
<td>2025-05-14</td>
<td>bioRxiv</td>
<td>preprint</td>
<td>10.1101/2025.05.14.654049</td>
<td>Cold Spring Harbor Laboratory</td>
<td>https://www.biorxiv.org/content/10.1101/2025.05.14.654049v1</td>
</tr>
<tr>
<td>Pairwise</td>
<td>Pairwise Attention: Leveraging Mass Differences to Enhance De Novo Sequencing of Mass Spectra</td>
<td>2025-03-28</td>
<td>bioRxiv</td>
<td>preprint</td>
<td>10.1101/2025.03.28.645943</td>
<td>Cold Spring Harbor Laboratory</td>
<td>https://www.biorxiv.org/content/10.1101/2025.03.28.645943v1</td>
</tr>
<tr>
<td>DiNovo</td>
<td>DiNovo: high-coverage, high-confidence de novo peptide sequencing using mirror proteases and deep learning</td>
<td>2025-03-20</td>
<td>bioRxiv</td>
<td>preprint</td>
<td>10.1101/2025.03.20.643920</td>
<td>Cold Spring Harbor Laboratory</td>
<td>https://www.biorxiv.org/content/10.1101/2025.03.20.643920v1</td>
</tr>
<tr>
<td>PepGo</td>
<td>PepGo: a deep learning and tree search-based model for de novo peptide sequencing</td>
<td>2025-02-24</td>
<td>bioRxiv</td>
<td>preprint</td>
<td>10.1101/2025.02.24.640018</td>
<td>Cold Spring Harbor Laboratory</td>
<td>https://www.biorxiv.org/content/10.1101/2025.02.24.640018v1</td>
</tr>
</table>




```sql
SELECT COUNT(*) AS unique_authors
FROM author
```




<table>
<tr>
<th>unique_authors</th>
</tr>
<tr>
<td>54</td>
</tr>
</table>




```sql
SELECT COUNT(*) AS unique_affiliations
FROM affiliation
```




<table>
<tr>
<th>unique_affiliations</th>
</tr>
<tr>
<td>22</td>
</tr>
</table>


